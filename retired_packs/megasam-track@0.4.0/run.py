"""Video -> three colmap4d application components; no SHARP inference."""
import hashlib
import json
import math
import os
from pathlib import Path
from fractions import Fraction
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


def timestamps(params, indexes, frames, time_base, digest, identity_key="video_sha256"):
    mode = params.get('timestamp_mode', 'exposure_metadata')
    meta = dict(colmap4d_spec='1.0', time_unit='ns', time_convention='mid_exposure',
                points_t_method='track_centroid')
    if mode == 'exposure_metadata':
        path = params.get('exposure_metadata_path')
        if not path:
            raise ValueError('exposure_metadata_path required for exposure_metadata')
        data = json.loads(Path(path).read_text())
        if data.get(identity_key) != digest:
            raise ValueError(f'Exposure metadata {identity_key} mismatch')
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
                              selected_decoded_indexes=indexes)
    meta['conversion'][identity_key] = digest
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
    camera_path = sparse / 'cameras.bin'
    cameras = cio.read_cameras_bin(camera_path) if camera_path.exists() else cio.read_cameras_txt(sparse / 'cameras.txt')
    if (sparse / 'images.bin').exists():
        image_rows = cio.read_images_bin(sparse / 'images.bin')
    else:
        # Native tracker emits blank observation lines. Preserve the paired text
        # records; colmap4d's fallback reader currently discards blank lines.
        lines = [line.strip() for line in (sparse / 'images.txt').read_text().splitlines()
                 if not line.lstrip().startswith('#')]
        image_rows = {}
        i = 0
        while i < len(lines):
            if not lines[i]:
                i += 1
                continue
            row = lines[i].split()
            if len(row) != 10 or i + 1 >= len(lines):
                raise ValueError('Malformed native image record')
            ident = int(row[0])
            if ident in image_rows:
                raise ValueError('Duplicate native image ID')
            image_rows[ident] = cio.Image(ident,row[9],int(row[8]),
                                          list(map(float,row[1:5])),list(map(float,row[5:8])))
            i += 2
    ordered = sorted(image_rows.values(), key=lambda im: im.name)
    frame_paths = sorted((work / 'frames').glob('frame_*.png'))
    if len(ordered) != len(times) or len(frame_paths) != len(times):
        raise ValueError('Frame/camera/time count mismatch')
    if [Path(im.name).stem for im in ordered] != [p.stem for p in frame_paths]:
        raise ValueError('COLMAP image names do not match ordered decoded frames')
    cams, points, pixels = [empty_output(outputs[k]) for k in ('cams', 'points', 'images')]
    (pixels / 'images').mkdir()
    cio.write_cameras_bin(cams / 'cameras.bin', cameras)
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
            camera = cameras[old.camera_id]
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


VIDEO_SUFFIXES = {'.mp4','.mov','.mkv','.avi','.webm','.m4v'}


def input_kind(source):
    if source.is_file() and source.suffix.lower() in VIDEO_SUFFIXES:
        return 'video', source
    if not source.is_dir():
        raise ValueError('frames must be a video-source or flat image array directory')
    entries = [p for p in source.iterdir() if not p.name.startswith('.')]
    videos = [p for p in entries if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
    images = []
    for entry in entries:
        if not entry.is_file() or entry in videos: continue
        try:
            with Image.open(entry) as candidate:
                candidate.verify()
            images.append(entry)
        except (OSError, ValueError, SyntaxError):
            pass
    if len(videos) == 1 and not images and all(p == videos[0] or p.suffix == '.json' for p in entries):
        return 'video', videos[0]
    if images and len(images) == len(entries):
        return 'images', source
    raise ValueError('Ambiguous/invalid frames: expected one video or only image leaves')


def frame_identity(files):
    """Bind metadata to the whole ordered array, before selection."""
    records = []
    for file in files:
        h = hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
        records.append({'name': file.name, 'sha256': h.hexdigest()})
    return hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def xmp_exposure(file):
    import xml.etree.ElementTree as ET
    with Image.open(file) as image:
        blobs = [image.info[k] for k in ('xmp','XML:com.adobe.xmp') if k in image.info]
        header = b'http://ns.adobe.com/xap/1.0/\0'
        blobs += [value[len(header):] for marker,value in getattr(image,'applist',[])
                  if marker == 'APP1' and value.startswith(header)]
    values = {}
    for blob in blobs:
        for element in ET.fromstring(blob).iter():
            for key,value in element.attrib.items():
                if key.startswith('{https://hololab.dev/ns/rig/1.0/}'):
                    name=key.split('}',1)[1]
                    if name in values and values[name]!=value: raise ValueError('Conflicting XMP exposure fields')
                    values[name]=value
    relevant = {'wallClockEpochNs','exposureNs','timebase'}
    if not relevant.intersection(values): return None
    if not relevant <= values.keys(): raise ValueError('Incomplete XMP exposure metadata')
    exposure = int(values['exposureNs'])
    if exposure < 0 or not values['timebase'].strip(): raise ValueError('Invalid XMP exposure/timebase')
    domain = {'server_epoch':'hololab_clocksync_server_epoch'}.get(values['timebase'],values['timebase'])
    return ns(ns(int(values['wallClockEpochNs'])) + exposure//2), domain


def prepare_frames(p, source, skip, cap, work):
    files = sorted(source.iterdir(), key=lambda f: f.name)
    files = [f for f in files if not f.name.startswith('.')]
    indexes = list(range(0,len(files),skip))
    if cap: indexes=indexes[:cap]
    if len(indexes)<2: raise ValueError('MegaSAM needs at least two selected frames')
    digest = frame_identity(files)
    if p.get('exposure_metadata_path'):
        times,meta=timestamps(dict(p,timestamp_mode='exposure_metadata'),indexes,[],None,digest,'frames_sha256')
    else:
        exposures=[xmp_exposure(files[i]) for i in indexes]
        if any(item is not None for item in exposures):
            if any(item is None for item in exposures): raise ValueError('Partial XMP coverage; no synthetic fallback')
            domains={item[1] for item in exposures}
            if len(domains)!=1: raise ValueError('Mixed XMP clock domains')
            times=[item[0] for item in exposures]
            meta=dict(colmap4d_spec='1.0',time_unit='ns',time_convention='mid_exposure',
                      clock_domain=domains.pop(),points_t_method='track_centroid',
                      conversion=dict(timestamp_mode='exposure_metadata',exposure_source='image_xmp',
                                      estimated=False,frames_sha256=digest,selected_decoded_indexes=indexes))
        else:
            fps=p.get('frames_fps')
            if type(fps) not in (int,float) or not math.isfinite(fps) or fps<=0:
                raise ValueError('frames_fps must be explicitly positive when frames have no exposure metadata')
            rate=Fraction(str(fps))
            times,meta=timestamps(dict(timestamp_mode='synthetic_uniform',fps_num=rate.numerator,fps_den=rate.denominator),
                                  indexes,[],None,digest,'frames_sha256')
    size=None
    for j,i in enumerate(indexes):
        with Image.open(files[i]) as image:
            image.load()
            if image.mode not in ('RGB','RGBA') or getattr(image,'n_frames',1)!=1:
                raise ValueError('Frames must be single 8-bit SDR RGB/RGBA images')
            if size is not None and image.size!=size: raise ValueError('Frames must share a resolution')
            size=image.size
            # Embedded profiles are color-converted; untagged input is declared SDR sRGB.
            if image.info.get('icc_profile'):
                import io
                from PIL import ImageCms
                image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(image.info['icc_profile'])),
                                                ImageCms.createProfile('sRGB'),outputMode='RGB')
            image.convert('RGB').save(work/'frames'/f'frame_{j:06d}.png')
    meta['conversion']['input_kind']='image_array'
    meta['conversion']['pixels']=dict(output='sRGB RGB8',untagged_assumption='sRGB',alpha='discarded')
    return times,meta


def prepare_video(p, video, skip, cap, work):
    hasher = hashlib.sha256()
    with video.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    digest = hasher.hexdigest()
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_frames','-show_streams','-show_entries',
        'frame=pts:stream=time_base,color_transfer,color_primaries,color_space,color_range','-of','json',str(video)], timeout=60))
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
    # Convert the declared SDR transfer/primaries to sRGB, not just YUV to RGB.
    # Untagged video follows an explicit recorded BT.709 limited-range assumption.
    color = {key: stream.get(key, 'unknown') for key in
             ('color_transfer','color_primaries','color_space','color_range')}
    defaults = dict(color_transfer='bt709',color_primaries='bt709',color_space='bt709',color_range='tv')
    assumed = [key for key in color if color[key] == 'unknown']
    for key in assumed: color[key] = defaults[key]
    if color['color_space'] not in ('bt709','smpte170m','bt470bg') or color['color_range'] not in ('tv','pc'):
        raise ValueError('Unsupported video matrix/range; provide SDR YUV video')
    meta['conversion']['pixels'] = dict(input=color, assumed_fields=assumed, output='sRGB RGB8')
    color_filter = (f"colorspace=ispace={color['color_space']}:iprimaries={color['color_primaries']}:"
                    f"itrc={color['color_transfer']}:irange={color['color_range']}:"
                    "space=bt709:primaries=bt709:trc=srgb:range=pc:format=yuv444p12,format=rgb24")
    subprocess.run(['ffmpeg','-nostdin','-v','error','-noautorotate','-i',str(video),
        '-map','0:v:0','-vf',f'select=not(mod(n\\,{skip})),{color_filter}','-vsync','0','-frames:v',str(len(indexes)),
        '-start_number','0','-pix_fmt','rgb24',str(work/'frames/frame_%06d.png')],check=True,timeout=300)
    if len(list((work/'frames').glob('*.png'))) != len(indexes):
        raise ValueError('Decoder count differs from probed selection')
    return times, meta


def main(config):
    p = dict(config.get('params', {}))
    allowed = {'megasam_repo','tracker_python','sharp_repo','skip','max_frames','depth_min',
               'pts_stride','timestamp_mode','exposure_metadata_path','fps_num','fps_den','frames_fps'}
    if set(p)-allowed:
        raise ValueError(f'Unknown parameters: {set(p)-allowed}')
    skip = integer(p.get('skip',1),'skip',1)
    cap = integer(p.get('max_frames',0),'max_frames',0)
    stride = integer(p.get('pts_stride',2),'pts_stride',1)
    depth_min = p.get('depth_min',.001)
    if type(depth_min) not in (int,float) or not math.isfinite(depth_min) or depth_min < 0:
        raise ValueError('depth_min must be finite and nonnegative')
    source = Path(config['inputs']['frames']).resolve()
    kind, source = input_kind(source)
    repo, sharp, python = [Path(p[k]).resolve() for k in ('megasam_repo','sharp_repo','tracker_python')]
    if not repo.is_dir() or not (sharp/'per-frame/video_to_colmap.py').is_file() or not python.is_file():
        raise ValueError('Invalid algorithm paths')
    roots = [Path(config['outputs']['4d_'+k]).resolve() for k in ('cams','points','images')]
    scratch = Path(config['scratch']).resolve()
    protected = [repo,sharp,source,python]
    if p.get('exposure_metadata_path'):
        protected.append(Path(p['exposure_metadata_path']).resolve())
    for i,a in enumerate(roots+[scratch]):
        for b in (roots+[scratch])[i+1:]+protected:
            if a == b or a in b.parents or b in a.parents:
                raise ValueError('Output/scratch/source paths must not overlap')
    for root in roots:
        if root.exists() and any(root.iterdir()):
            raise ValueError('Outputs must be empty')
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='megasam-040-', dir=scratch))
    (work/'frames').mkdir()
    times, meta = (prepare_video(p, source, skip, cap, work) if kind == 'video'
                   else prepare_frames(p, source, skip, cap, work))
    private = work/'megasam-repo'
    subprocess.run(['cp','-rL','--reflink=auto',str(repo),str(private)],check=True,timeout=300)
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME'): env.pop(key,None)
    env.update(PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', MPLBACKEND='Agg')
    print('[ITER 1/3] native MegaSAM chain',flush=True)
    subprocess.run([str(python),str(sharp/'per-frame/video_to_colmap.py'),'--convert-only',
        '--track_megasam','--megasam-root',str(private),'-o',str(work)],cwd=sharp,env=env,check=True)
    print('[ITER 2/3] standardize components',flush=True)
    result = export(work,{k: config['outputs']['4d_'+k] for k in ('cams','points','images')},times,meta,depth_min,stride)
    print(json.dumps(result)); print('[ITER 3/3] done',flush=True)


if __name__ == '__main__':
    main(json.load(sys.stdin))
