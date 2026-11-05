from pathlib import Path
import json,hashlib,time,concurrent.futures
O=Path('hololab/reports/merge-030-performance')
a=Path(json.loads((O/'baseline-output.json').read_text())['absolute_path']);b=Path(json.loads((O/'benchmark-output.json').read_text())['absolute_path'])
def files(root):return {str(p.relative_to(root)):p for p in root.rglob('*') if p.is_file()}
fa,fb=files(a),files(b);assert fa.keys()==fb.keys();records=[];start=time.perf_counter()
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
 return h.hexdigest()
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for key in sorted(fa):
  if key == '.hololab-metadata.json': continue  # gateway job/handle provenance, not model content
  ha,hb=list(pool.map(digest,[fa[key],fb[key]]));assert ha==hb,key
  records.append({'file':key,'bytes':fa[key].stat().st_size,'baseline_sha256':ha,'fast_sha256':hb,'equal':True,'independent_inode':fa[key].stat().st_ino!=fb[key].stat().st_ino})
(O/'output-equivalence.json').write_text(json.dumps({'files':records,'all_model_content_equal':True,'excluded_runtime_metadata':['.hololab-metadata.json'],'seconds':time.perf_counter()-start},indent=2));print('All files byte-identical by SHA256:',len(records),'bytes:',sum(x['bytes'] for x in records),flush=True)
