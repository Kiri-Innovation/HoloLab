from copy import deepcopy
import pytest
from hololab.gateway.array_views import compile_array_views
from hololab.gateway.workflows import WorkflowGraph, GraphNode, GraphEdge, packs_by_key_from_catalog, resolved_port_types
from hololab.gateway.port_type_ir import ArrayType, ObjectType
from tests.test_array_view_contract import capability


def fixture():
    scalar = dict(tags=['colmap4d'], arrayed=False, dim_labels=[])
    array = dict(tags=['colmap4d'], arrayed=True, dim_labels=['frame'])
    def pack(name, inputs, outputs, **more):
        return dict(name=name, version='0.1.0', inputs=inputs, outputs=outputs, **more)
    catalog = [
        pack('test/source', {}, {'out': scalar}),
        pack('test/consume', {'in': scalar}, {'out': scalar}),
        pack('builtin/slice', {'in': dict(tags=['any'], arrayed=True)},
             {'out':dict(tags=['any'], arrayed=True, tags_from='in', dim_labels_from_input='in', dim_labels_drop_outer=0)}),
        pack('col4d/exposure-groups', {'model': scalar}, {'frames':array}, type_capabilities={'colmap4d': capability()}),
        pack('col4d/restore-exposure-groups', {'frames': array}, {'model':scalar}),
    ]
    graph = WorkflowGraph(nodes=[GraphNode(id=n, algorithm_name=p, algorithm_version='0.1.0', assigned_node_id='cpu')
                               for n,p in [('source','test/source'),('slice','builtin/slice'),('sink','test/consume')]],
                          edges=[GraphEdge(id='a',source='source',sourceHandle='out',target='slice',targetHandle='in'),
                                 GraphEdge(id='b',source='slice',sourceHandle='out',target='sink',targetHandle='in')])
    return graph, catalog


def test_plan_materializes_and_restores_without_mutating_source():
    graph, catalog = fixture()
    before = graph.model_dump()
    result, plans = compile_array_views(graph, catalog)
    assert graph.model_dump() == before
    assert [p['direction'] for p in plans] == ['to_array','from_array']
    assert result.pinned_params == graph.pinned_params
    packs = packs_by_key_from_catalog(catalog)
    by_id = {n.id:n for n in result.nodes}
    source_ir,_ = resolved_port_types(by_id['source'], packs['test/source','0.1.0'],result,by_id,packs)
    assert isinstance(source_ir.outputs['out'].views.aggregate,ObjectType)
    slice_ir,_ = resolved_port_types(by_id['slice'], packs['builtin/slice','0.1.0'],result,by_id,packs)
    assert isinstance(slice_ir.outputs['out'].views.aggregate,ArrayType)
    assert slice_ir.outputs['out'].runtime.axes.evidence.status == 'unknown'
    assert compile_array_views(graph, catalog)[0].model_dump() == result.model_dump()
    again, additional = compile_array_views(result, catalog)
    assert not additional
    assert again.model_dump() == result.model_dump()


def test_missing_provider_is_error():
    graph,catalog = fixture()
    with pytest.raises(ValueError,match='unavailable'):
        compile_array_views(graph,catalog[:-1])


def test_signature_mismatch_is_error():
    graph,catalog = fixture()
    catalog[-1]['outputs']['model'] = dict(tags=['image'], arrayed=False)
    with pytest.raises(ValueError,match='signature'):
        compile_array_views(graph,catalog)


def test_same_tag_does_not_imply_restore_provenance():
    graph,catalog = fixture()
    catalog[2]['outputs']['out'].pop('tags_from')
    catalog[2]['outputs']['out'].update(tags=['colmap4d'],dim_labels=['frame'])
    with pytest.raises(ValueError,match='provenance'):
        compile_array_views(graph,catalog)


def test_terminal_array_is_restored():
    graph,catalog=fixture()
    graph.nodes=[n for n in graph.nodes if n.id!='sink']
    graph.edges=[e for e in graph.edges if e.id!='b']
    result,plans=compile_array_views(graph,catalog)
    assert [p['direction'] for p in plans]==['to_array','from_array']
    assert len(result.nodes)==4


def test_competing_view_names_are_not_chosen_by_install_order():
    graph,catalog=fixture()
    views=catalog[-2]['type_capabilities']['colmap4d']['array_views']
    views['another']=deepcopy(views['exposure_groups'])
    with pytest.raises(ValueError,match='one candidate'):
        compile_array_views(graph,catalog)


def test_preparation_freezes_plan_and_provider_version():
    from hololab.gateway.preparation import resolve_recipe, context_revision
    graph,catalog=fixture()
    resolved=resolve_recipe(graph,catalog)
    assert len(resolved.execution['array_views'])==2
    assert len(resolved.nodes)==5
    assert all(v == [] for v in resolved.pinned_params.values())  # ordinary parameter pin freezing only
    revision=context_revision(graph,catalog)
    assert revision==resolved.execution["revision"]
    assert revision==context_revision(compile_array_views(graph,catalog)[0],catalog)
    changed=deepcopy(catalog)
    changed[-2]['type_capabilities']['colmap4d']['array_views']['exposure_groups']['version']=1
    changed[-2]['type_capabilities']['colmap4d']['array_views']['exposure_groups']['to_array']['version']='0.2.0'
    changed.append({**deepcopy(changed[-2]),'version':'0.2.0','type_capabilities':{}})
    assert context_revision(graph,changed)!=revision
    assert resolved.execution['array_views'][0]['provider']['version']=='0.1.0'


def test_compiled_graph_is_valid_under_unchanged_type_checker():
    from hololab.gateway.workflows import validate_snapshot
    graph,catalog=fixture()
    compiled,plans=compile_array_views(graph,catalog)
    issues=validate_snapshot(compiled,packs_by_key=packs_by_key_from_catalog(catalog),
                             online_node_ids={'cpu'},packs_offered_by_node={'cpu':{(p['name'],p['version']) for p in catalog}})
    assert not issues


def test_fanout_collects_real_elements_before_restore():
    graph,catalog=fixture()
    node=next(n for n in graph.nodes if n.id=='slice')
    node.algorithm_name='test/map';node.arrayed_toggle=True
    scalar=dict(tags=['colmap4d'],arrayed=False,dim_labels=[])
    catalog.append(dict(name='test/map',version='0.1.0',arrayable=True,inputs={'in':scalar},outputs={'out':scalar}))
    result,plans=compile_array_views(graph,catalog)
    assert [p['direction'] for p in plans]==['to_array','from_array']
    assert next(n for n in result.nodes if n.id=='slice').arrayed_toggle is True
