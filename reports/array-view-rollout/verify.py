"""Read-only evidence for the 41,990,400-point source and reconstructed models."""
import hashlib,json,struct
from pathlib import Path
base=Path('/cloud/cloud-ssd1/Kiri4DGS/output/array-view-validation-20261105')
source=Path('/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/a31dba7e-9ca5-4c04-8d4b-c915a5a30937/model')
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(4*1024*1024):h.update(b)
 return h.hexdigest()
files={}
for p in sorted(source.rglob('*')):
 rel=p.relative_to(source)
 if not p.is_file() or any(x.startswith('.') for x in rel.parts):continue
 q=base/'restored'/rel
 if p.name=='time_meta.json':assert json.loads(p.read_text())==json.loads(q.read_text())
 else:
  a,b=digest(p),digest(q);assert a==b,str(rel)
  files[str(rel)]=dict(source_sha256=a,restored_sha256=b,bytes=p.stat().st_size)
def times(root):
 data=(root/'sparse/0/times.bin').read_bytes()
 return list(struct.iter_unpack('<Iq',data[8:]))
original=times(source);selected=times(base/'subset30-32')
assert selected==original[30:32]
assert times(base/'index30')==original[30:31]
with (base/'subset30-32/sparse/0/points3D.bin').open('rb') as f:count=struct.unpack('<Q',f.read(8))[0]
assert count==1036800
result=dict(source=str(source),restored=str(base/'restored'),binary_and_pixels_byte_identical=True,
            files=files,metadata_semantically_equal=True,index30=times(base/'index30'),slice30_32=selected,subset_points=count)
Path(__file__).with_name('verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2));print('identical files:',len(files))
