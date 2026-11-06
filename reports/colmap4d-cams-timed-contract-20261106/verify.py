"""Read-only real component verification; writes only to a fresh artifact directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml
from hololab.manifest.schema import Manifest

repo = Path(__file__).resolve().parents[2]
source = repo.parent/'output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/03fb2084-8f8c-43c2-9bda-aa27955ede38/4d_cams'
artifacts = Path(tempfile.mkdtemp(prefix='cams-array-', dir=repo.parent/'tmp'))
report = dict(source=str(source), artifacts=str(artifacts), schema=[], commands=[])

def run(script, *args):
    command = [sys.executable, str(script), *map(str, args)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    report['commands'].append(dict(argv=command, stdout=result.stdout.strip()))

for direction in ('to', 'from'):
    pack = repo/f'community_packs/colmap4d-cams-{direction}-array@0.1.0'
    m = Manifest.model_validate(yaml.safe_load((pack/'manifest.yaml').read_text()))
    report['schema'].append(dict(name=m.name, version=m.version, valid=True))
    run(pack/'convert.py', direction+'_array', '--input', source if direction=='to' else artifacts/'array',
        '--output', artifacts/'array' if direction=='to' else artifacts/'restored')

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            h.update(block)
    return h.hexdigest()

report['files'] = {name:dict(source_sha256=sha(source/name), restored_sha256=sha(artifacts/'restored'/name),
                           bytes=(source/name).stat().st_size)
                   for name in ('cameras.bin', 'images.bin', 'times.bin', 'time_meta.json')}
assert all(v['source_sha256']==v['restored_sha256'] for v in report['files'].values())
run(repo/'hololab/builtin_packs/interval-pick@0.1.0/interval_pick.py', '--input', artifacts/'array',
    '--output', artifacts/'picked', '--interval', '10')
run(repo/'community_packs/colmap4d-cams-from-array@0.1.0/convert.py', 'from_array',
    '--input', artifacts/'picked', '--output', artifacts/'subset')
# Verify exact subset identity/time with the package reader, and feed one real leaf
# through the other session's existing drop-time converter, read-only.
import colmap4d
from colmap4d.arrays import read_image_headers
entries = json.loads((artifacts/'array/.camera-view.json').read_text())['entries']
selected = [entries[p.name] for p in sorted((artifacts/'picked').iterdir())]
assert set(read_image_headers(artifacts/'subset/images.bin')) == {e['image_id'] for e in selected}
assert colmap4d.read_times_bin(artifacts/'subset/times.bin') == {e['image_id']:e['timestamp_ns'] for e in selected}
for path in sorted((artifacts/'picked').iterdir()):
    data = json.loads(path.read_text())
    assert set(data) == {'model','width','height','params','qvec','tvec','timestamp_ns'}
    assert type(data['timestamp_ns']) is int
    assert data['timestamp_ns'] == entries[path.name]['timestamp_ns']
drop = repo/'community_packs/single-cam-drop-time@0.1.0'
m = Manifest.model_validate(yaml.safe_load((drop/'manifest.yaml').read_text()))
assert m.inputs['camera'].tags == ['colmap4d-single-cam']
assert m.outputs['camera'].tags == ['colmap-single-cam']
report['schema'].append(dict(name=m.name, version=m.version, valid=True))
report['existing_drop_time_script_sha256'] = sha(drop/'drop_time.py')
chosen = sorted((artifacts/'picked').iterdir())[0]
run(drop/'drop_time.py', '--camera', chosen, '--output', artifacts/'timeless.json')
timed = json.loads(chosen.read_text())
assert json.loads((artifacts/'timeless.json').read_text()) == {
    k:v for k,v in timed.items() if k != 'timestamp_ns'}
report['leaf_type'] = 'colmap4d-single-cam'
report['drop_time_output_type'] = 'colmap-single-cam'
report['roundtrip_byte_equal'] = True
report['subset'] = selected
(repo/'reports/colmap4d-cams-timed-contract-20261106/verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
