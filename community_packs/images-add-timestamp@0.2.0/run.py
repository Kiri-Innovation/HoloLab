import json, shutil, sys
from pathlib import Path
from PIL import Image
from colmap4d import colmap_io as cio, sidecar

def run(c):
    inputs=c['inputs']; out=Path(next(iter(c['outputs'].values())))
    if out.exists() and any(out.iterdir()): raise ValueError('Output must be empty')
    if 'timed_images' in inputs:
        root=Path(inputs['timed_images']); records=cio.read_images_bin(root/'images.bin') if (root/'images.bin').exists() else None; times=sidecar.read_times_bin(root/'times.bin',strict=True)
        ordered=sorted(times,key=lambda i:(times[i],i)); names={str(i):im.name for i,im in records.items()} if records else json.loads((root/'image_names.json').read_text()); sources=[root/'images'/names[str(i)] for i in ordered]
        out.mkdir(parents=True,exist_ok=True)
        for n,source in enumerate(sources):
            with Image.open(source) as im: im.convert('RGB').save(out/f'p_{n:016d}.png')
    else:
        root=Path(inputs['timestamps']); times=sidecar.read_times_bin(root/'times.bin',strict=True)
        meta=json.loads((root/'time_meta.json').read_text())
        if (meta.get('time_unit'),meta.get('time_convention'))!=('ns','mid_exposure') or not meta.get('clock_domain'): raise ValueError('Invalid clock metadata')
        ordered=sorted(times,key=lambda i:(times[i],i)); sources=sorted(p for p in Path(inputs['images']).iterdir() if not p.name.startswith('.'))
        if len(sources)!=len(ordered): raise ValueError('Frame count must exactly match timestamps')
        records=cio.read_images_bin(root/'images.bin') if (root/'images.bin').exists() else None
        out.mkdir(parents=True,exist_ok=True);(out/'images').mkdir()
        mapping={}
        for n,(iid,source) in enumerate(zip(ordered,sources)):
            name=records[iid].name if records else f'frame_{iid}.png'
            target=out/'images'/name
            if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('Unsafe image name')
            target.parent.mkdir(parents=True,exist_ok=True)
            with Image.open(source) as im: im.convert('RGB').save(target,format='PNG')
            mapping[str(iid)]=name
        for name in ('times.bin','time_meta.json','images.bin','cameras.bin'):
            if (root/name).exists(): shutil.copyfile(root/name,out/name)
        (out/'image_names.json').write_text(json.dumps(mapping))
    return len(sources)
if __name__=='__main__': print(run(json.load(sys.stdin)))
