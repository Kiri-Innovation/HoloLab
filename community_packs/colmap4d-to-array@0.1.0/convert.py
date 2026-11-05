"""Exact exposure-group views for canonical single-source binary colmap4d.

No point objects or source symlinks. Read-only source, bounded writes, original
ID/ns/observation records preserved. General multi-track models fail explicitly.
"""
import argparse
import hashlib
import json
import shutil
import struct
import time
from pathlib import Path, PurePosixPath
import numpy as np
from PIL import Image as Pixels
from colmap4d.arrays import read_image_headers
from colmap4d import colmap_io as cio

CHUNK = 262144
POINT = np.dtype(dict(names=['id','xyz','rgb','error','n','image','index'],
    formats=['<u8',('<f8',3),('u1',3),'<f8','<u8','<u4','<u4'], offsets=[0,8,32,35,43,51,55],itemsize=59))
PT = np.dtype([('id','<u8'),('t','<i8')])
TM = np.dtype([('id','<u4'),('t','<i8')])
OBS = np.dtype([('x','<f8'),('y','<f8'),('pid','<u8')])


def require(ok, why):
    if not ok:
        raise ValueError(why)


def table(path, dtype):
    with path.open('rb') as f:
        raw=f.read(8)
    require(len(raw)==8, f'truncated {path}')
    n=struct.unpack('<Q',raw)[0]
    require(path.stat().st_size==8+n*dtype.itemsize, f'noncanonical/truncated {path.name}')
    return np.memmap(path,mode='r',offset=8,dtype=dtype,shape=(n,)) if n else np.empty(0,dtype=dtype)


def write_table(path, rows):
    with path.open('wb') as f:
        f.write(struct.pack('<Q',len(rows)))
        for start in range(0,len(rows),CHUNK):
            f.write(rows[start:start+CHUNK].tobytes())


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(4*1024*1024): h.update(block)
    return h.hexdigest()


def files_digest(root):
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not any(x.startswith('.') for x in p.relative_to(root).parts)}


def images_index(path):
    headers=read_image_headers(path)
    records={}; observations={}
    with path.open('rb') as f:
        require(struct.unpack('<Q',f.read(8))[0]==len(headers),'duplicate images')
        for iid,im in headers.items():
            start=f.tell(); head=f.read(64)
            require(len(head)==64 and struct.unpack_from('<I',head)[0]==iid,'invalid image header')
            while True:
                c=f.read(1);require(c,'truncated image name')
                if c==b'\0': break
            raw=f.read(8);require(len(raw)==8,'truncated observations')
            n=struct.unpack('<Q',raw)[0]; offset=f.tell()
            require(offset+n*24<=path.stat().st_size,'truncated observations')
            observations[iid]=np.memmap(path,mode='r',offset=offset,dtype=OBS,shape=(n,)) if n else np.empty(0,dtype=OBS)
            f.seek(n*24,1);records[iid]=(start,f.tell()-start)
        require(f.tell()==path.stat().st_size,'trailing image bytes')
    return headers,records,observations


def validate(root):
    sparse=root/'sparse/0'
    pts=table(sparse/'points3D.bin',POINT);pt=table(sparse/'points_t.bin',PT)
    ts=table(sparse/'times.bin',TM)
    headers,records,obs=images_index(sparse/'images.bin')
    require(headers,'empty image model')
    cameras=cio.read_cameras_bin(sparse/'cameras.bin')
    require(len(ts)==len(headers) and set(map(int,ts['id']))==set(headers),'times coverage')
    times={int(row['id']):int(row['t']) for row in ts}
    require(len(pt)==len(pts),'points_t coverage')
    previous=0
    for start in range(0,len(pts),CHUNK):
        a=pts[start:start+CHUNK];t=pt[start:start+CHUNK]
        require(np.all(a['id']>0) and (not len(a) or a['id'][0]>previous) and np.all(a['id'][1:]>a['id'][:-1]),'point IDs must be unique and sorted')
        previous=int(a['id'][-1]) if len(a) else previous
        require(np.all(a['n']==1),'exposure_groups requires single-source tracks')
        require(np.array_equal(a['id'],t['id']),'points_t ID coverage/order')
        require(np.isfinite(a['xyz']).all() and np.isfinite(a['error']).all() and np.all(a['error']>=0),'nonfinite/invalid geometry')
        for iid in np.unique(a['image']):
            iid=int(iid);require(iid in times,'unknown track image')
            mask=a['image']==iid;idx=a['index'][mask]
            require(np.all(idx<len(obs[iid])),'track index out of range')
            require(np.array_equal(obs[iid]['pid'][idx],a['id'][mask]),'nonreciprocal point track')
            require(np.all(t['t'][mask]==times[iid]),'source-frame time mismatch')
    for c in cameras.values():
        require(c.width>0 and c.height>0 and np.isfinite(c.params).all(), 'invalid camera')
    names=set();references=0
    for iid,im in headers.items():
        rel=PurePosixPath(im.name)
        require(im.name and not rel.is_absolute() and '..' not in rel.parts and '\\' not in im.name and rel.as_posix()==im.name and im.name not in names,'unsafe/duplicate NAME')
        names.add(im.name)
        require(im.camera_id in cameras,'unknown camera')
        pixel=root/'images'/im.name
        require(pixel.is_file(),'missing pixels')
        require(np.isfinite(im.qvec+im.tvec).all() and abs(sum(q*q for q in im.qvec)-1)<1e-5,'invalid image pose')
        with Pixels.open(pixel) as pix:
            pix.load();camera=cameras[im.camera_id]
            require(pix.mode in ('RGB','RGBA') and pix.size==(camera.width,camera.height),'pixel format/resolution mismatch')
        a=obs[iid]
        for start in range(0,len(a),CHUNK):
            chunk=a[start:start+CHUNK]
            require(np.isfinite(chunk['x']).all() and np.isfinite(chunk['y']).all(),'nonfinite observation')
            mask=chunk['pid']!=2**64-1;pids=chunk['pid'][mask]
            indices=np.searchsorted(pts['id'],pids)
            require(np.all(indices<len(pts)),'unknown observation point')
            rows=pts[indices]
            require(np.array_equal(rows['id'],pids) and np.all(rows['image']==iid) and np.array_equal(rows['index'],np.arange(start,start+len(chunk))[mask]),'nonreciprocal image observation')
            references+=len(pids)
    require({p.relative_to(root/'images').as_posix() for p in (root/'images').rglob('*') if p.is_file()}==names,'pixel NAME bijection')
    require(references==len(pts),'observation coverage')
    meta=json.loads((sparse/'time_meta.json').read_text())
    require(meta.get('time_unit')=='ns' and meta.get('clock_domain'),'explicit ns clock domain required')
    return pts,pt,ts,headers,records,meta


def copy_image_records(path, output, records):
    with path.open('rb') as src,output.open('wb') as dst:
        dst.write(struct.pack('<Q',len(records)))
        for offset,size in records:
            src.seek(offset)
            while size:
                block=src.read(min(size,4*1024*1024));require(block,'truncated image record')
                dst.write(block);size-=len(block)


def empty_output(source, output):
    require(source.resolve()!=output.resolve() and source.resolve() not in output.resolve().parents and output.resolve() not in source.resolve().parents,'overlapping input/output')
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),'output must be empty')
    output.mkdir(parents=True,exist_ok=True)


def to_array(source, output):
    empty_output(source,output)
    print('validate source and reciprocal tracks',flush=True)
    pts,pt,ts,headers,records,meta=validate(source)
    namespace=hashlib.sha256(json.dumps(files_digest(source),sort_keys=True).encode()).hexdigest()
    groups={}
    for row in ts: groups.setdefault(int(row['t']),[]).append(int(row['id']))
    members=[]
    for position,(t,iids) in enumerate(sorted(groups.items())):
        name=f'p_{position:016d}';root=output/name;sparse=root/'sparse/0';sparse.mkdir(parents=True)
        shutil.copyfile(source/'sparse/0/cameras.bin',sparse/'cameras.bin')
        shutil.copyfile(source/'sparse/0/time_meta.json',sparse/'time_meta.json')
        copy_image_records(source/'sparse/0/images.bin',sparse/'images.bin',[records[i] for i in sorted(iids)])
        write_table(sparse/'times.bin',ts[np.isin(ts['id'],iids)])
        with (sparse/'points3D.bin').open('wb') as pf,(sparse/'points_t.bin').open('wb') as tf:
            pf.write(bytes(8));tf.write(bytes(8));count=0
            for start in range(0,len(pts),CHUNK):
                mask=np.isin(pts['image'][start:start+CHUNK],iids)
                pf.write(pts[start:start+CHUNK][mask].tobytes());tf.write(pt[start:start+CHUNK][mask].tobytes());count+=int(mask.sum())
            pf.seek(0);pf.write(struct.pack('<Q',count));tf.seek(0);tf.write(struct.pack('<Q',count))
        for iid in iids:
            target=root/'images'/headers[iid].name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/'images'/headers[iid].name,target)
        evidence=dict(format='hololab.array-view.element.v1',namespace=namespace,position=position,
                      timestamp_ns=t,image_ids=sorted(iids),view='exposure_groups',provider='col4d/colmap4d-to-array@0.1.0',files=files_digest(root))
        (root/'.array-view.json').write_text(json.dumps(evidence,sort_keys=True))
        members.append(dict(position=position,path=name,timestamp_ns=t,image_ids=sorted(iids)))
        print(f'group {position+1}/{len(groups)}: {count} points',flush=True)
    (output/'.array-view.json').write_text(json.dumps(dict(format='hololab.array-view.v1',namespace=namespace,members=members),sort_keys=True))
    return dict(groups=len(groups),points=len(pts),namespace=namespace)


def from_array(source, output):
    empty_output(source,output)
    roots=sorted(p for p in source.iterdir() if not p.name.startswith('.'))
    require(roots,'cannot restore an empty selection')
    sparse=output/'sparse/0';sparse.mkdir(parents=True)
    namespace=None;meta=None;seen_images={};all_points=[];all_times=[];point_times=[]
    for root in roots:
        evidence=json.loads((root/'.array-view.json').read_text())
        require(evidence.get('format')=='hololab.array-view.element.v1','missing view provenance')
        require(evidence['files']==files_digest(root),'element content conflict: source view was modified')
        if namespace is None: namespace=evidence['namespace']
        require(namespace==evidence['namespace'],'source namespace conflict; automatic remap forbidden')
        pts,pt,ts,headers,records,m=validate(root)
        if meta is None:
            meta=m;shutil.copyfile(root/'sparse/0/cameras.bin',sparse/'cameras.bin')
        require(meta==m,'clock domain/time metadata conflict')
        require(sha(sparse/'cameras.bin')==sha(root/'sparse/0/cameras.bin'),'camera ID content conflict')
        all_points.append(pts);point_times.append(pt);all_times.extend(ts.tolist())
        for iid,im in headers.items():
            offset,size=records[iid]
            with (root/'sparse/0/images.bin').open('rb') as f:
                f.seek(offset);h=hashlib.sha256();remaining=size
                while remaining:
                    b=f.read(min(remaining,4*1024*1024));require(b,'truncated image record');h.update(b);remaining-=len(b)
            signature=(h.hexdigest(),sha(root/'images'/im.name))
            if iid in seen_images:
                require(seen_images[iid][0]==signature,'image ID content conflict')
                continue
            seen_images[iid]=(signature,root,offset,size,im.name)
    points=np.concatenate(all_points);times=np.concatenate(point_times)
    order=np.argsort(points['id'],kind='stable');points=points[order];times=times[order]
    duplicates=np.flatnonzero(points['id'][1:]==points['id'][:-1])+1
    for start in range(0,len(duplicates),CHUNK):
        idx=duplicates[start:start+CHUNK]
        require(points[idx].tobytes()==points[idx-1].tobytes() and times[idx].tobytes()==times[idx-1].tobytes(),'point ID content conflict')
    keep=np.ones(len(points),dtype=bool);keep[duplicates]=False
    write_table(sparse/'points3D.bin',points[keep]);write_table(sparse/'points_t.bin',times[keep])
    tmap={}
    for iid,t in all_times:
        require(iid not in tmap or tmap[iid]==t,'image timestamp conflict');tmap[iid]=t
    write_table(sparse/'times.bin',np.array(sorted(tmap.items()),dtype=TM))
    names=set()
    with (sparse/'images.bin').open('wb') as out:
        out.write(struct.pack('<Q',len(seen_images)))
        for iid,(_,root,offset,size,name) in sorted(seen_images.items()):
            require(name not in names,'image NAME collision');names.add(name)
            with (root/'sparse/0/images.bin').open('rb') as f:
                f.seek(offset)
                while size:
                    b=f.read(min(size,4*1024*1024));require(b,'truncated image record');out.write(b);size-=len(b)
            dest=output/'images'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/'images'/name,dest)
    (sparse/'time_meta.json').write_text(json.dumps(meta,indent=2)+'\n')
    validate(output)
    (output/'.array-view.json').write_text(json.dumps(dict(namespace=namespace,restored_from=[str(p.name) for p in roots])))
    return dict(groups=len(roots),images=len(seen_images),points=int(keep.sum()),namespace=namespace)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['to_array','from_array']);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();start=time.perf_counter();result=globals()[a.operation](a.input,a.output);result['seconds']=time.perf_counter()-start
    print(json.dumps(result),flush=True)
