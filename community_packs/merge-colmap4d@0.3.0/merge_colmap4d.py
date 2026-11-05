"""Array validation and byte-preserving component assembly; no library monkeypatches."""
import argparse
import json
import math
import resource
import shutil
import struct
import time
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image as Pixels
import colmap4d
from colmap4d import colmap_io as cio
from colmap4d.arrays import read_point_arrays, read_image_headers
from colmap4d.validate import check_device_camera_ids_unique, exit_code
import strict_legacy as legacy

CHUNK = 524288
SAMPLES = 4096
require = legacy.require


class LegacyLayout(Exception):
    """Valid but non-fast layout: use the preserved full normalizer."""


def stage(n, message):
    print(f'[ITER {n}/5] {message}', flush=True)


def count(path):
    with path.open('rb') as f:
        raw = f.read(8)
    require(len(raw) == 8, f'Truncated count: {path.name}')
    return struct.unpack('<Q', raw)[0]


def sidecar(path, ids, wide):
    dtype = np.dtype([('id', '<u8' if wide else '<u4'), ('t', '<i8')])
    n = count(path)
    require(path.stat().st_size == 8 + n*dtype.itemsize, f'Invalid sidecar size: {path.name}')
    require(n == len(ids), f'{path.name} must exactly cover IDs')
    if not n:
        return np.empty(0, dtype=dtype)
    rows = np.memmap(path, mode='r', dtype=dtype, offset=8, shape=(n,))
    for start in range(0, n, CHUNK):
        end = min(n, start+CHUNK)
        if not np.array_equal(rows['id'][start:end], ids[start:end]):
            # Preserve acceptance of valid unsorted sidecars. The strict fallback
            # still rejects duplicates/missing/foreign IDs before writing anything.
            raise LegacyLayout(f'{path.name} noncanonical ID order/coverage')
    return rows


def point_records(path):
    n = count(path)
    payload = path.stat().st_size - 8
    if not n:
        require(payload == 0, 'Trailing point bytes')
        return np.empty(0), 0
    stride, rem = divmod(payload, n)
    if rem or stride < 51 or (stride-51) % 8:
        raise LegacyLayout('variable-length tracks')
    k = (stride-51)//8
    dtype = np.dtype({'names':['id','error','count','track'],
        'formats':['<u8','<f8','<u8',('<u4',(k,2))],
        'offsets':[0,35,43,51], 'itemsize':stride})
    rows = np.memmap(path, mode='r', dtype=dtype, offset=8, shape=(n,))
    for start in range(0,n,CHUNK):
        if not np.all(rows['count'][start:start+CHUNK] == k):
            raise LegacyLayout('nonuniform tracks')
    return rows, k


def image_observations(path, headers):
    """Only per-image offsets/views, never Python per-observation objects."""
    require(count(path) == len(headers), 'Duplicate image IDs')
    views = {}
    obs_type = np.dtype([('x','<f8'),('y','<f8'),('pid','<u8')])
    with path.open('rb') as f:
        f.seek(8)
        for iid in headers:
            head = f.read(64)
            require(len(head)==64 and struct.unpack_from('<I',head)[0]==iid, 'Invalid image header')
            while True:
                char=f.read(1)
                require(bool(char),'Unterminated image name')
                if char==b'\0': break
            raw=f.read(8);require(len(raw)==8,'Truncated observation count')
            n=struct.unpack('<Q',raw)[0];offset=f.tell()
            require(offset+n*24<=path.stat().st_size,'Truncated observations')
            views[iid]=np.memmap(path,mode='r',dtype=obs_type,offset=offset,shape=(n,)) if n else np.empty(0,dtype=obs_type)
            f.seek(n*24,1)
        require(f.tell()==path.stat().st_size,'Trailing image bytes')
    return views


def validate_pixels(cameras, images, root):
    names=set();root=Path(root)/'images'
    require(root.is_dir(),'Missing images/ directory')
    for c in cameras.values():
        mid=cio._MODEL_NAME_TO_ID.get(c.model)
        require(c.width>0 and c.height>0 and mid is not None, 'Invalid camera')
        require(len(c.params)==cio._MODEL_NUM_PARAMS[mid] and all(map(math.isfinite,c.params)), 'Invalid camera parameters')
        require(c.params[0]>0 and (c.model not in ('PINHOLE','OPENCV','OPENCV_FISHEYE','FULL_OPENCV','FOV','THIN_PRISM_FISHEYE') or c.params[1]>0),'Invalid focal length')
    for im in images.values():
        name=im.name;p=PurePosixPath(name)
        require(name and not p.is_absolute() and '\\' not in name and '..' not in p.parts and p.as_posix()==name and name not in names,'Unsafe/duplicate image NAME')
        names.add(name)
        require(0<im.id<2**32 and im.camera_id in cameras,'Invalid image/camera ID')
        require(all(map(math.isfinite, im.qvec+im.tvec)) and abs(sum(q*q for q in im.qvec)-1)<1e-5,'Invalid pose')
        f=root/name
        require(f.is_file() and not f.is_symlink() and root.resolve() in f.resolve().parents,'Missing or external/symlink pixel file')
        with Pixels.open(f) as pix:
            pix.load();c=cameras[im.camera_id]
            require(pix.mode in ('RGB','RGBA') and getattr(pix,'n_frames',1)==1,'Expected single RGB/RGBA image')
            require(pix.size==(c.width,c.height),'Image/camera resolution mismatch')
    require({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}==names,'Pixel NAME bijection mismatch')


def fast_validate(cams, points, pixels):
    paths={stem:legacy.selected(root,stem) for root,stems in [(cams,['cameras','images','times']),(points,['points3D','points_t'])] for stem in stems}
    if any(p.suffix!='.bin' for p in paths.values()):
        raise LegacyLayout('text input requires canonical binary normalization')
    records,k=point_records(paths['points3D'])
    record_count=len(records)
    del records  # release the mapped pages before the library's bulk buffer allocates
    cameras=legacy.table(cams,'cameras')  # tiny camera table, full validation
    images=read_image_headers(paths['images'])  # no observation payload read by library
    require(cameras and images,'Empty cameras/images')
    if list(images)!=sorted(images) or list(cameras)!=sorted(cameras):
        raise LegacyLayout('noncanonical camera/image ordering')
    times=sidecar(paths['times'],np.array(list(images),dtype=np.uint32),False)
    ts={iid:int(t) for iid,t in zip(images,times['t'])}
    meta=legacy.merge_meta(legacy.metadata(cams),legacy.metadata(points))
    require(exit_code(check_device_camera_ids_unique(meta),strict=True)==0,'Conflicting device CAMERA_ID')
    obs=image_observations(paths['images'],images)
    stage(1, 'array readers: IDs/geometry (no Point3D or observation objects)')
    ids,xyz,rgb=read_point_arrays(paths['points3D'])
    n=len(ids)
    require(n==record_count,'Duplicate point IDs')
    dense=True
    for start in range(0,n,CHUNK):
        end=min(n,start+CHUNK);a=ids[start:end]
        require(np.all((a>0)&(a<cio.INVALID_POINT3D_ID)),'Invalid point ID')
        require(np.isfinite(xyz[start:end]).all(),'Nonfinite point geometry')
        dense=dense and bool(np.all(a==np.arange(start,end,dtype=np.uint64)+ids[0]))
    del xyz,rgb
    records,_=point_records(paths['points3D'])
    for start in range(0,n,CHUNK):
        end=min(n,start+CHUNK)
        err=records['error'][start:end]
        require(np.isfinite(err).all() and np.all(err>=0),'Invalid point error')
        if not np.array_equal(records['id'][start:end],ids[start:end]):
            raise LegacyLayout('noncanonical point ordering')
    pt=sidecar(paths['points_t'],ids,True)
    stage(2,f'validate coverage/tracks: {n} points, {len(images)} images, track width {k}')
    def locate(pids):
        if not n:
            require(not len(pids),'Observation references missing point')
            return np.empty(0,dtype=np.int64)
        if dense:
            require(np.all((pids>=ids[0])&(pids<=ids[-1])),'Observation references unknown point')
            return (pids-ids[0]).astype(np.int64)
        pos=np.searchsorted(ids,pids)
        require(np.all(pos<n),'Observation references unknown point')
        require(np.array_equal(ids[pos],pids),'Observation references unknown point')
        return pos
    references=0
    for iid,view in obs.items():
        for start in range(0,len(view),CHUNK):
            v=view[start:start+CHUNK]
            require(np.isfinite(v['x']).all() and np.isfinite(v['y']).all(),'Nonfinite observation')
            valid=v['pid']!=cio.INVALID_POINT3D_ID
            pos=locate(v['pid'][valid]);references+=len(pos)
            if k==1:
                track=records['track'][pos,0,:]
                require(np.all(track[:,0]==iid) and np.array_equal(track[:,1],np.flatnonzero(valid)+start),'Nonreciprocal observation/track')
                require(np.all(pt['t'][pos]==ts[iid]),'Source-frame point time mismatch')
            elif k==0:
                require(not len(pos),'Observation references trackless point')
    if k==1:
        # Reciprocity means each point can appear at only its one fixed (image,index).
        # Equality of total references therefore proves complete point coverage.
        require(references==n,'Missing reciprocal point observations')
    else:
        require(k>0 or not n or meta.get('points_t_method')!='track_centroid','Empty track cannot claim track_centroid')
        # General fixed-width tracks: all entries have valid image/index bounds;
        # reciprocal relations/centroid times are deterministically sampled below.
        iids=np.array(sorted(obs),dtype=np.uint32)
        lengths=np.array([len(obs[int(i)]) for i in iids],dtype=np.uint64)
        for start in range(0,n,CHUNK):
            tr=records['track'][start:start+CHUNK]
            for col in range(k):
                at=np.searchsorted(iids,tr[:,col,0]);require(np.all(at<len(iids)),'Unknown track image')
                require(np.array_equal(iids[at],tr[:,col,0]) and np.all(tr[:,col,1]<lengths[at]),'Invalid track image/index')
        for row in np.unique(np.linspace(0,n-1,min(n,SAMPLES),dtype=np.int64)) if n else []:
            track=[(int(a),int(b)) for a,b in records['track'][row]]
            require(len(set(track))==len(track),'Duplicate track observation (sample)')
            for iid,index in track:
                require(int(obs[iid]['pid'][index])==int(ids[row]),'Nonreciprocal point track (sample)')
            if track and meta.get('points_t_method')=='track_centroid':
                require(int(pt['t'][row])==(sum(ts[i] for i,_ in track)+len(track)//2)//len(track),'Track-centroid time mismatch (sample)')
        for iid,view in obs.items():
            for index in np.unique(np.linspace(0,len(view)-1,min(len(view),SAMPLES),dtype=np.int64)) if len(view) else []:
                pid=int(view['pid'][index])
                if pid==cio.INVALID_POINT3D_ID:continue
                row=int(locate(np.array([pid],dtype=np.uint64))[0])
                require(any(int(a)==iid and int(b)==index for a,b in records['track'][row]),'Nonreciprocal image observation (sample)')
    stage(3,'pixel bijection, resolution and full image decoding')
    validate_pixels(cameras,images,pixels)
    return paths,images,meta,n,('exhaustive-single-track' if k==1 else 'sampled-multi-track')


def assemble(cams,points,images,output,strict_validate=False):
    started=time.perf_counter();output=Path(output).resolve()
    for source in (cams,points,images):
        source=Path(source).resolve()
        require(output!=source and output not in source.parents and source not in output.parents,'Output overlaps input')
    require(not output.exists() or not any(output.iterdir()),'Output must be empty')
    stage(0,'strict full validation' if strict_validate else 'fast array validation')
    fallback=None
    if not strict_validate:
        try:paths,ims,meta,n,coverage=fast_validate(cams,points,images)
        except LegacyLayout as exc:
            fallback=str(exc);stage(1,'full-validation fallback: '+fallback);strict_validate=True
    if strict_validate:
        # Preserve the 0.2.0 validator, writer and final validate_full(strict).
        result=legacy.assemble(cams,points,images,output)
        coverage='full-legacy';n=result['points'];image_count=result['images']
    else:
        stage(4,'byte-preserving binary and image copy (independent files, no hardlinks)')
        sparse=output/'sparse/0';sparse.mkdir(parents=True)
        for stem,source in paths.items():shutil.copyfile(source,sparse/(stem+'.bin'))
        colmap4d.write_time_meta(sparse/'time_meta.json',meta)
        for im in ims.values():
            target=output/'images'/im.name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(Path(images)/'images'/im.name,target)
        (output/'.hololab-done').write_text('ok\n');image_count=len(ims)
    elapsed=time.perf_counter()-started
    stage(5,f'done in {elapsed:.3f}s; validation={coverage}')
    return dict(images=image_count,points=n,validation=coverage,strict_validate=strict_validate,
                fallback=fallback,seconds=elapsed,max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cams','points','images','output'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--strict-validate',action='store_true')
    a=parser.parse_args()
    print(json.dumps(assemble(a.cams,a.points,a.images,a.output,a.strict_validate)),flush=True)
