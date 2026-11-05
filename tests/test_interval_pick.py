import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
from hololab.manifest import load_manifest

PACK=Path(__file__).resolve().parents[1]/'hololab/builtin_packs/interval-pick@0.1.0'
spec=importlib.util.spec_from_file_location('interval_pick',PACK/'interval_pick.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.mark.parametrize('n', [0,1,2,5,8])
@pytest.mark.parametrize('interval', [1,2,3,20])
@pytest.mark.parametrize('start', [0,1,4,9])
def test_selection(n,interval,start,tmp_path):
    src=tmp_path/'in';src.mkdir()
    for i in range(n): (src/f'item_{i:03}').write_bytes(str(i).encode())
    (src/'.metadata').write_text('ignored')
    out=tmp_path/'out';result=module.pick(src,out,interval,start)
    expected=list(range(start,n,interval))
    assert result['output_count']==len(expected)==max(0,(n-start+interval-1)//interval)
    assert sorted(p.name for p in out.iterdir())==[f'item_{i:03}' for i in expected]
    for i in expected:
        p=out/f'item_{i:03}';assert p.is_symlink();assert p.read_bytes()==str(i).encode()

@pytest.mark.parametrize('interval,start',[(0,0),(-1,0),(True,0),(1,-1),(1,False),(1.5,0)])
def test_bad_params(interval,start,tmp_path):
    src=tmp_path/'in';src.mkdir()
    with pytest.raises(ValueError):module.pick(src,tmp_path/'out',interval,start)
    assert not (tmp_path/'out').exists()


def test_bundles_nested_axes_and_name_order(tmp_path):
    src=tmp_path/'in';src.mkdir()
    for name in ('z','10','2','a'):
        (src/name/'inner').mkdir(parents=True);(src/name/'inner/data').write_text(name)
    out=tmp_path/'out';module.pick(src,out,2)
    assert sorted(p.name for p in out.iterdir())==['10','a']
    assert (out/'a/inner/data').read_text()=='a'
    with pytest.raises(ValueError,match='empty'):module.pick(src,out)
    with pytest.raises(ValueError,match='overlap'):module.pick(src,src/'nested')
    assert not (src/'nested').exists()


def test_matches_slice_and_stdlib_cli(tmp_path):
    src=tmp_path/'in';src.mkdir()
    for i in range(7):(src/str(i)).write_text(str(i))
    for script,out,args in [(PACK/'interval_pick.py',tmp_path/'pick',['--interval','3','--start','1']),
                            (PACK.parent/'slice@0.1.0/slice.py',tmp_path/'slice',['--step','3','--start','1'])]:
        subprocess.run([sys.executable,'-I','-S',str(script),'--input',str(src),'--output',str(out),*args],check=True,capture_output=True)
    assert {p.name:os.readlink(p) for p in (tmp_path/'pick').iterdir()}=={p.name:os.readlink(p) for p in (tmp_path/'slice').iterdir()}


def test_manifest_identity_and_parameter_bounds():
    from hololab.bundled import verified_record
    m,_=load_manifest(PACK/'manifest.yaml');assert verified_record(PACK/'manifest.yaml')['origin']=='bundled'
    assert m.runtime.mode=='host';assert m.inputs['in'].tags==['any']
    assert m.outputs['out'].tags_from=='in';assert m.outputs['out'].dim_labels_from_input=='in'
    assert m.outputs['out'].dim_labels_drop_outer==0
    for value in (0,-2,True,1.5):
        with pytest.raises(ValueError,match='interval'):m.params['interval'].validate_scalar_value('interval',value)


@pytest.mark.parametrize('tag,axes',[('image',['frame']),('arbitrary-bundle',['sample','view'])])
def test_type_and_axis_inheritance(tag,axes):
    from hololab.gateway.workflows import (GraphEdge,GraphNode,InputPortView,OutputPortView,PackHandle,
        WorkflowGraph,effective_output_dim_labels,effective_output_tags)
    m,_=load_manifest(PACK/'manifest.yaml')
    pack=PackHandle(inputs={'in':InputPortView(tags=('any',),arrayed=True,required=True)},outputs={'out':OutputPortView(
        tags=('any',),arrayed=True,tags_from=m.outputs['out'].tags_from,
        dim_labels_from_input=m.outputs['out'].dim_labels_from_input,dim_labels_drop_outer=0)})
    source=PackHandle(inputs={},outputs={'out':OutputPortView(tags=(tag,),arrayed=True,dim_labels=tuple(axes))})
    nodes=[GraphNode(id='src',algorithm_name='test/source',algorithm_version='0.1.0'),
           GraphNode(id='pick',algorithm_name=m.name,algorithm_version=m.version)]
    graph=WorkflowGraph(nodes=nodes,edges=[GraphEdge(id='e',source='src',sourceHandle='out',target='pick',targetHandle='in')])
    byid={n.id:n for n in nodes};packs={('test/source','0.1.0'):source,(m.name,m.version):pack}
    assert effective_output_dim_labels(nodes[1],pack,'out',graph,byid,packs)==axes
    assert effective_output_tags(nodes[1],pack,'out',graph,byid,packs)==[tag]
