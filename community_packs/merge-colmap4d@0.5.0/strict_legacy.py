"""Strict application-profile assembly. No XMP parsing or timestamp inference."""
import argparse
import copy
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import struct
from PIL import Image as Pixels
from colmap4d import colmap_io as cio
import colmap4d
from colmap4d.validate import exit_code


def require(ok, message):
    if not ok: raise ValueError(message)


def selected(root, stem):
    root = Path(root)
    for ext in ('bin','txt'):
        path = root / f'{stem}.{ext}'
        if path.is_file(): return path
    raise ValueError(f'Missing {stem} in {root}')


def binary_ids(path, stem):
    """Check exact byte shape and duplicate IDs before the dict-based base reader."""
    ids = set()
    with path.open('rb') as f:
        size = path.stat().st_size
        def read(fmt):
            n = struct.calcsize('<'+fmt); raw = f.read(n)
            require(len(raw)==n, f'Truncated {path.name}')
            return struct.unpack('<'+fmt,raw)
        def skip(n):
            require(n >= 0 and f.tell()+n <= size, f'Truncated {path.name}')
            f.seek(n,1)
        count, = read('Q')
        require(count <= size, 'Impossible record count')
        for _ in range(count):
            if stem == 'cameras':
                ident, model, w, h = read('IiQQ')
                require(model in cio._MODEL_NUM_PARAMS, 'Unknown camera model')
                skip(8*cio._MODEL_NUM_PARAMS[model])
            elif stem == 'images':
                row=read('I7dI'); ident=row[0]
                while True:
                    char=f.read(1)
                    require(bool(char), 'Unterminated image NAME')
                    if char==b'\0': break
                n,=read('Q'); skip(24*n)
            else:
                row=read('QdddBBBdQ'); ident=row[0]; skip(8*row[-1])
            require(ident not in ids, f'Duplicate {stem} ID {ident}')
            ids.add(ident)
        require(f.tell()==size, f'Trailing bytes in {path.name}')
    return ids


def table(root, stem):
    print(f'[strict] reading {stem}', flush=True)
    path=selected(root,stem)
    if path.suffix=='.bin':
        ids=binary_ids(path,stem)
        result=getattr(cio,f'read_{stem}_bin')(path)
    else:
        # Preserve blank POINTS2D lines; the fallback library strips them.
        lines=[line.strip() for line in path.read_text().splitlines() if not line.lstrip().startswith('#')]
        result={}; ids=set()
        i=0
        while i<len(lines):
            if not lines[i]: i+=1; continue
            row=lines[i].split(); ident=int(row[0]); i+=1
            require(ident not in ids, f'Duplicate {stem} ID {ident}'); ids.add(ident)
            if stem=='images':
                require(len(row)==10 and i<len(lines),'Malformed image record')
                obs=lines[i].split(); i+=1
                require(len(obs)%3==0,'Malformed POINTS2D')
                result[ident]=cio.Image(ident,row[9],int(row[8]),list(map(float,row[1:5])),list(map(float,row[5:8])),
                    [(float(obs[j]),float(obs[j+1])) for j in range(0,len(obs),3)],
                    [int(obs[j+2]) if int(obs[j+2])!=-1 else cio.INVALID_POINT3D_ID for j in range(0,len(obs),3)])
            elif stem=='cameras':
                result[ident]=cio.Camera(ident,row[1],int(row[2]),int(row[3]),list(map(float,row[4:])))
            else:
                require(len(row)>=8 and (len(row)-8)%2==0,'Malformed point track')
                result[ident]=cio.Point3D(ident,list(map(float,row[1:4])),list(map(int,row[4:7])),float(row[7]),
                    [(int(row[j]),int(row[j+1])) for j in range(8,len(row),2)])
    bound=2**64-1 if stem=='points3D' else 2**32
    require(all(0<ident<bound for ident in ids),f'Invalid {stem} ID')
    return result


def time_table(root,stem,ids):
    path=selected(root,stem)
    if path.suffix == '.bin':
        with path.open('rb') as f: header = f.read(8)
        require(len(header) == 8, 'Truncated time sidecar header')
        count, = struct.unpack('<Q', header)
        require(path.stat().st_size == 8 + count * (12 if stem == 'times' else 16), 'Invalid time sidecar size')
    data=getattr(colmap4d,f'read_{stem}_{path.suffix[1:]}')(path,strict=True)
    require(set(data)==set(ids), f'{stem} must exactly cover IDs')
    require(all(type(t) is int and -(2**63)<=t<2**63 for t in data.values()),'Invalid int64 time')
    return data


def metadata(root):
    data=json.loads((Path(root)/'time_meta.json').read_text())
    require(isinstance(data,dict),'time_meta must be an object')
    for key,value in [('colmap4d_spec','1.0'),('time_unit','ns'),('time_convention','mid_exposure')]:
        require(data.get(key)==value,f'Invalid time_meta {key}')
    require(isinstance(data.get('clock_domain'),str) and bool(data['clock_domain'].strip()),'Missing clock_domain')
    return data


def merge_meta(a,b,path='time_meta'):
    out=copy.deepcopy(a)
    for key,value in b.items():
        if key not in out: out[key]=copy.deepcopy(value)
        elif isinstance(out[key],dict) and isinstance(value,dict): out[key]=merge_meta(out[key],value,path+'.'+key)
        else: require(type(out[key]) is type(value) and out[key]==value,f'Metadata conflict: {path}.{key}')
    return out


def validate(cams,points,pixels):
    cameras=table(cams,'cameras'); images=table(cams,'images'); pts=table(points,'points3D')
    require(bool(cameras) and bool(images),'Empty cameras/images component')
    times=time_table(cams,'times',images); pt=time_table(points,'points_t',pts)
    meta=merge_meta(metadata(cams),metadata(points))
    for c in cameras.values():
        require(c.width>0 and c.height>0,'Invalid image resolution')
        mid=cio._MODEL_NAME_TO_ID.get(c.model)
        require(mid is not None and len(c.params)==cio._MODEL_NUM_PARAMS[mid] and all(map(math.isfinite,c.params)), 'Invalid camera parameters')
        require(c.params[0]>0 and (c.model not in ('PINHOLE','OPENCV','OPENCV_FISHEYE','FULL_OPENCV','FOV','THIN_PRISM_FISHEYE') or c.params[1]>0),'Invalid focal length')
    names=set(); pixel_root=Path(pixels)/'images'; observations=set()
    require(pixel_root.is_dir(),'Missing images/ directory')
    for image in images.values():
        print(f'[strict] checking image {image.id}', flush=True)
        name=image.name; path=PurePosixPath(name)
        require(bool(name) and not path.is_absolute() and '\\' not in name and '..' not in path.parts
                and path.as_posix()==name and name not in names,'Unsafe/duplicate image NAME')
        names.add(name)
        require(image.camera_id in cameras,'Unknown CAMERA_ID')
        require(all(map(math.isfinite,image.qvec+image.tvec)) and abs(sum(q*q for q in image.qvec)-1)<1e-5,'Invalid pose')
        require(len(image.xys)==len(image.point3D_ids),'Mismatched POINTS2D')
        for idx,(xy,pid) in enumerate(zip(image.xys,image.point3D_ids)):
            require(all(map(math.isfinite,xy)),'Nonfinite observation')
            if pid==cio.INVALID_POINT3D_ID: continue
            require(pid in pts and (image.id,idx) in pts[pid].track,'Nonreciprocal image observation')
            observations.add((pid,image.id,idx))
        file=pixel_root/name
        require(file.is_file() and not file.is_symlink() and pixel_root.resolve() in file.resolve().parents,'Missing or external/symlink pixel file')
        with Pixels.open(file) as decoded:
            decoded.load(); c=cameras[image.camera_id]
            require(decoded.mode in ('RGB','RGBA') and getattr(decoded,'n_frames',1)==1,'Expected single 8-bit RGB/RGBA image')
            require(decoded.size==(c.width,c.height),'Image/camera resolution mismatch')
    files={p.relative_to(pixel_root).as_posix() for p in pixel_root.rglob('*') if p.is_file()}
    require(files==names,'images/ and image NAMEs must be a bijection')
    for number, point in enumerate(pts.values()):
        if number % 1000000 == 0: print(f'[strict] checking point {number}/{len(pts)}', flush=True)
        require(len(point.xyz)==3 and all(map(math.isfinite,point.xyz)) and math.isfinite(point.error) and point.error>=0,'Invalid point geometry')
        require(len(point.rgb)==3 and all(0<=v<=255 for v in point.rgb),'Invalid RGB')
        require(len(set(point.track))==len(point.track),'Duplicate track observation')
        for iid,idx in point.track:
            require((point.id,iid,idx) in observations,'Nonreciprocal point track')
        if len(point.track)==1:
            require(pt[point.id]==times[point.track[0][0]],'Source-frame point time mismatch')
        if meta.get('points_t_method')=='track_centroid':
            require(bool(point.track),'Empty track cannot claim track_centroid')
            t=(sum(times[iid] for iid,_ in point.track)+len(point.track)//2)//len(point.track)
            require(pt[point.id]==t,'Track-centroid point time mismatch')
    return cameras,images,pts,times,pt,meta


def assemble(cams,points,images,output):
    output=Path(output).resolve()
    for source in (cams,points,images):
        source=Path(source).resolve()
        require(output!=source and output not in source.parents and source not in output.parents,'Output overlaps input')
    require(not output.exists() or not any(output.iterdir()),'Output must be empty')
    cameras,ims,pts,times,pt,meta=validate(cams,points,images)
    print('[strict] writing normalized model', flush=True)
    sparse=output/'sparse/0'; sparse.mkdir(parents=True)
    # Normalize storage to binary, preserving every ID, observation and integer ns.
    cio.write_cameras_bin(sparse/'cameras.bin',cameras)
    cio.write_images_bin(sparse/'images.bin',ims)
    cio.write_points3D_bin(sparse/'points3D.bin',pts)
    colmap4d.write_times_bin(sparse/'times.bin',times)
    colmap4d.write_points_t_bin(sparse/'points_t.bin',pt)
    colmap4d.write_time_meta(sparse/'time_meta.json',meta)
    for image in ims.values():
        target=output/'images'/image.name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path(images)/'images'/image.name,target)
    print('[strict] final validate_full (observations/tracks skipped by library reader)', flush=True)
    problems=colmap4d.validate_full(sparse)
    require(exit_code(problems,strict=True)==0,'validate_full(strict): '+str(problems))
    (output/'.hololab-done').write_text('ok\n')
    return dict(images=len(ims),points=len(pts),strict_problems=len(problems))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cams','points','images','output'): parser.add_argument('--'+name,required=True,type=Path)
    a=parser.parse_args()
    print(json.dumps(assemble(a.cams,a.points,a.images,a.output)))
