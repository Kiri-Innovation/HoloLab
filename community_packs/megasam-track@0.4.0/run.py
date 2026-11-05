"""Video -> three colmap4d application components; no SHARP inference."""
import hashlib
import json
import math
import os
from pathlib import Path
from fractions import Fraction
import shutil
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
from colmap4d import colmap_io as cio
from colmap4d import write_times_bin, write_time_meta


def integer(value, name, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f'{name}: invalid integer {value!r}')
    return value


def ns(value):
    integer(value, 'timestamp')
    if not -(1 << 63) <= value < (1 << 63):
        raise ValueError('timestamp overflows int64')
    return value


def timestamps(params, indexes, frames, time_base, digest):
    mode = params.get('timestamp_mode', 'exposure_metadata')
    meta = dict(colmap4d_spec='1.0', time_unit='ns', time_convention='mid_exposure',
                points_t_method='track_centroid')
    if mode == 'exposure_metadata':
        path = params.get('exposure_metadata_path')
        if not path:
            raise ValueError('exposure_metadata_path required for exposure_metadata')
        data = json.loads(Path(path).read_text())
        if data.get('video_sha256') != digest:
            raise ValueError('Exposure metadata video_sha256 mismatch')
        domain = data.get('clock_domain')
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError('Exposure clock_domain required')
        mapping = {}
        for record in data['frames']:
            idx = integer(record['decoded_index'], 'decoded_index', 0)
            if idx in mapping:
                raise ValueError('Duplicate decoded_index')
            direct = 'mid_exposure_ns' in record
            interval = 'exposure_start_ns' in record or 'exposure_ns' in record
            if direct == interval:
                raise ValueError('Choose exactly one exposure representation')
            t = ns(record['mid_exposure_ns']) if direct else ns(
                ns(record['exposure_start_ns']) + integer(record['exposure_ns'], 'exposure_ns', 0) // 2)
            mapping[idx] = t
        if not set(indexes) <= mapping.keys():
            raise ValueError('Exposure metadata does not cover selected frames')
        times = [mapping[i] for i in indexes]
    elif mode == 'pts_estimate':
        scale = Fraction(time_base) * 10**9
        if scale <= 0:
            raise ValueError('Invalid stream time_base')
        times = [ns(round(integer(frames[i]['pts'], 'actual PTS') * scale)) for i in indexes]
        domain = 'video_pts:' + digest
    elif mode == 'synthetic_uniform':
        num = integer(params.get('fps_num'), 'fps_num', 1)
        den = integer(params.get('fps_den'), 'fps_den', 1)
        times = [ns(round(Fraction(i * 10**9 * den, num))) for i in indexes]
        domain = 'synthetic_uniform:' + digest
    else:
        raise ValueError('Unknown timestamp_mode')
    meta['clock_domain'] = domain
    meta['conversion'] = dict(timestamp_mode=mode, estimated=mode != 'exposure_metadata',
                              video_sha256=digest, selected_decoded_indexes=indexes)
    return times, meta


def rotation(q):
    q = np.asarray(q, dtype=np.float64)
    if not np.isfinite(q).all() or abs(np.linalg.norm(q) - 1) > 1e-5:
        raise ValueError('Invalid COLMAP quaternion')
    w, x, y, z = q
    return np.array([[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                     [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                     [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]])


def empty_output(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f'Output must be empty: {path}')
    return path


def export(work, outputs, times, meta, depth_min=.001, pts_stride=2):
    """Use COLMAP world poses and metric Z depth. Stream points per frame."""
    work = Path(work)
    sparse = work / 'colmap' / 'sparse' / '0'
    model = cio.read_model(sparse)
    ordered = sorted(model.images.values(), key=lambda im: im.name)
    frame_paths = sorted((work / 'frames').glob('frame_*.png'))
    if len(ordered) != len(times) or len(frame_paths) != len(times):
        raise ValueError('Frame/camera/time count mismatch')
    if [Path(im.name).stem for im in ordered] != [p.stem for p in frame_paths]:
        raise ValueError('COLMAP image names do not match ordered decoded frames')
    cams, points, pixels = [empty_output(outputs[k]) for k in ('cams', 'points', 'images')]
    (pixels / 'images').mkdir()
    cio.write_cameras_bin(cams / 'cameras.bin', model.cameras)
    write_times_bin(cams / 'times.bin', {i+1: t for i, t in enumerate(times)})
    for folder in (cams, points):
        write_time_meta(folder / 'time_meta.json', meta)
    # Unaligned dtype is precisely the standard 51-byte header plus 8-byte source track.
    pdtype = np.dtype([('id','<u8'),('xyz','<f8',(3,)),('rgb','u1',(3,)),
                      ('error','<f8'),('n','<u8'),('image','<u4'),('idx','<u4')])
    odtype = np.dtype([('xy','<f8',(2,)),('id','<u8')])
    tdtype = np.dtype([('id','<u8'),('t','<i8')])
    count = 0
    with np.load(work / 'megasam_run' / 'original_resolution.npz', allow_pickle=False) as data, \
         open(points / 'points3D.bin','wb') as pf, open(points / 'points_t.bin','wb') as tf, \
         open(cams / 'images.bin','wb') as imf:
        depths = data['depths']
        if depths.ndim != 3 or len(depths) != len(times):
            raise ValueError('Expected depths[T,H,W] aligned to decoded frames')
        pf.write(struct.pack('<Q', 0)); tf.write(struct.pack('<Q', 0))
        imf.write(struct.pack('<Q', len(times)))
        for index, (old, frame, depth, t) in enumerate(zip(ordered, frame_paths, depths, times)):
            iid = index + 1
            camera = model.cameras[old.camera_id]
            if camera.model != 'PINHOLE' or len(camera.params) != 4:
                raise ValueError('MegaSAM export requires undistorted PINHOLE cameras')
            fx, fy, cx, cy = camera.params
            if not np.isfinite(camera.params).all() or min(fx, fy) <= 0:
                raise ValueError('Invalid intrinsics')
            with Image.open(frame) as image:
                rgb = np.asarray(image.convert('RGB'))
            if depth.shape != rgb.shape[:2] or depth.shape != (camera.height, camera.width):
                raise ValueError('Depth/pixels/camera resolution mismatch')
            v, u = np.mgrid[0:camera.height:pts_stride, 0:camera.width:pts_stride]
            z = depth[v, u]
            good = np.isfinite(z) & (z > depth_min)
            u, v, z = u[good], v[good], z[good].astype(np.float64)
            xyz_c = np.column_stack(((u-cx)*z/fx, (v-cy)*z/fy, z))
            r = rotation(old.qvec)
            xyz = (xyz_c - np.asarray(old.tvec)) @ r
            if not np.isfinite(xyz).all():
                raise ValueError('Nonfinite world coordinates')
            n = len(z)
            if n >= 2**32 or count+n >= 2**64-1:
                raise ValueError('COLMAP ID/index overflow')
            ids = np.arange(count+1, count+n+1, dtype=np.uint64)
            rec = np.zeros(n, pdtype)
            rec['id'], rec['xyz'], rec['rgb'] = ids, xyz, rgb[v,u]
            rec['n'], rec['image'], rec['idx'] = 1, iid, np.arange(n)
            pf.write(rec.tobytes())
            tr = np.empty(n, tdtype); tr['id'], tr['t'] = ids, t
            tf.write(tr.tobytes())
            name = f'f{index:06d}__cam0.png'
            imf.write(struct.pack('<I7dI', iid, *old.qvec, *old.tvec, old.camera_id))
            imf.write(name.encode() + b'\0' + struct.pack('<Q', n))
            obs = np.empty(n, odtype); obs['xy'], obs['id'] = np.column_stack((u,v)), ids
            imf.write(obs.tobytes())
            Image.fromarray(rgb).save(pixels / 'images' / name)
            count += n
        pf.seek(0); pf.write(struct.pack('<Q', count))
        tf.seek(0); tf.write(struct.pack('<Q', count))
    return dict(images=len(times), points=count)


def main(config):
    p = dict(config.get('params', {}))
    allowed = {'megasam_repo','tracker_python','sharp_repo','skip','max_frames','depth_min',
               'pts_stride','timestamp_mode','exposure_metadata_path','fps_num','fps_den'}
    if set(p)-allowed:
        raise ValueError(f'Unknown parameters: {set(p)-allowed}')
    skip = integer(p.get('skip',1),'skip',1)
    cap = integer(p.get('max_frames',0),'max_frames',0)
    stride = integer(p.get('pts_stride',2),'pts_stride',1)
    depth_min = p.get('depth_min',.001)
    if type(depth_min) not in (int,float) or not math.isfinite(depth_min) or depth_min < 0:
        raise ValueError('depth_min must be finite and nonnegative')
    video = Path(config['inputs']['video']).resolve()
    if video.is_dir():
        candidates = [f for f in video.iterdir() if f.is_file() and f.suffix.lower() in
                      {'.mp4','.mov','.mkv','.avi','.webm','.m4v'}]
        if len(candidates) != 1:
            raise ValueError('video-source must contain exactly one video')
        video = candidates[0]
    repo, sharp, python = [Path(p[k]).resolve() for k in ('megasam_repo','sharp_repo','tracker_python')]
    if not repo.is_dir() or not (sharp/'per-frame/video_to_colmap.py').is_file() or not python.is_file():
        raise ValueError('Invalid algorithm paths')
    roots = [Path(config['outputs'][k]).resolve() for k in ('cams','points','images')]
    scratch = Path(config['scratch']).resolve()
    protected = [repo,sharp,video,python]
    if p.get('exposure_metadata_path'):
        protected.append(Path(p['exposure_metadata_path']).resolve())
    for i,a in enumerate(roots+[scratch]):
        for b in (roots+[scratch])[i+1:]+protected:
            if a == b or a in b.parents or b in a.parents:
                raise ValueError('Output/scratch/source paths must not overlap')
    for root in roots:
        if root.exists() and any(root.iterdir()):
            raise ValueError('Outputs must be empty')
    digest = hashlib.file_digest(video.open('rb'), 'sha256').hexdigest()
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_frames','-show_streams','-show_entries',
        'frame=pts:stream=time_base,color_transfer,color_primaries','-of','json',str(video)], timeout=60))
    frames, stream = probe['frames'], probe['streams'][0]
    indexes = list(range(0,len(frames),skip))
    if cap: indexes = indexes[:cap]
    if len(indexes)<2:
        raise ValueError('MegaSAM needs at least two selected frames')
    times, meta = timestamps(p,indexes,frames,stream['time_base'],digest)
    # Reject untreated HDR rather than silently relabel it as sRGB.
    transfer = stream.get('color_transfer','unknown')
    if transfer not in ('unknown','bt709','iec61966-2-1','smpte170m'):
        raise ValueError(f'Unsupported transfer {transfer}; provide SDR sRGB video')
    if stream.get('color_primaries','unknown') not in ('unknown','bt709','smpte170m'):
        raise ValueError('Unsupported video primaries; provide SDR sRGB video')
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='megasam-040-', dir=scratch))
    (work/'frames').mkdir()
    subprocess.run(['ffmpeg','-nostdin','-v','error','-noautorotate','-i',str(video),
        '-map','0:v:0','-vf',f'select=not(mod(n\\,{skip}))','-vsync','0','-frames:v',str(len(indexes)),
        '-start_number','0','-pix_fmt','rgb24',str(work/'frames/frame_%06d.png')],check=True,timeout=300)
    if len(list((work/'frames').glob('*.png'))) != len(indexes):
        raise ValueError('Decoder count differs from probed selection')
    private = work/'megasam-repo'
    subprocess.run(['cp','-rL','--reflink=auto',str(repo),str(private)],check=True,timeout=300)
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME'): env.pop(key,None)
    env.update(PYTHONUNBUFFERED='1', MPLBACKEND='Agg')
    print('[ITER 1/3] native MegaSAM chain',flush=True)
    subprocess.run([str(python),str(sharp/'per-frame/video_to_colmap.py'),'--convert-only',
        '--track_megasam','--megasam-root',str(private),'-o',str(work)],cwd=sharp,env=env,check=True)
    print('[ITER 2/3] standardize components',flush=True)
    result = export(work,config['outputs'],times,meta,depth_min,stride)
    print(json.dumps(result)); print('[ITER 3/3] done',flush=True)


if __name__ == '__main__':
    main(json.load(sys.stdin))
