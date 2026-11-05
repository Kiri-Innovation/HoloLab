"""Compile type-owned array views to ordinary, inspectable provider nodes.

The original graph and handles are never mutated. The result uses the existing
port IR and normal execution/handle registration. No per-operation hooks exist.
"""
from __future__ import annotations

import hashlib
import json
from hololab.manifest.array_views import resolve_capabilities
from hololab.gateway.port_type_ir import ArrayType, ObjectType, compatible, from_legacy
from hololab.package_identity import package_key


def compile_array_views(graph, catalog):
    from hololab.gateway.workflows import (
        GraphNode, GraphEdge, packs_by_key_from_catalog, resolved_port_types,
        execution_topological_order,
    )
    result = graph.model_copy(deep=True)
    offers = resolve_capabilities([
        (f"{p['name']}@{p['version']}", p.get("type_capabilities", {})) for p in catalog
    ])
    if not offers:
        return result, []
    packs = packs_by_key_from_catalog(catalog)
    catalog_by_key = {package_key(p['name'], p['version']): p for p in catalog}
    plans = []
    lineage = {}
    nodes = {n.id: n for n in result.nodes}

    def match(a, b):
        return 'any' in a or 'any' in b or bool(set(a) & set(b))

    def fits(a, b, labels=True):
        return compatible(a, b, match, require_axis_label_match=labels)

    def port_types(node):
        pack = packs.get(package_key(node.algorithm_name, node.algorithm_version))
        if pack is None:
            return None
        return resolved_port_types(node, pack, result, nodes, packs)[0]

    def insert(edge, owner, tag, name, view, direction):
        hook = getattr(view, direction)
        provider = catalog_by_key.get(package_key(hook.name, hook.version))
        if provider is None:
            raise ValueError(f"array view provider unavailable: {hook.name}@{hook.version}")
        inp = provider.get('inputs', {}).get(hook.input)
        out = provider.get('outputs', {}).get(hook.output)
        if inp is None or out is None:
            raise ValueError('array view provider port unavailable')
        obj = ObjectType(tags=[tag])
        arr = from_legacy(view.array.tags, True, view.array.dim_labels)
        expected_in, expected_out = (obj, arr) if direction == 'to_array' else (arr, obj)
        for actual, expected in ((inp, expected_in), (out, expected_out)):
            actual_type = from_legacy(actual['tags'], actual.get('arrayed', False), actual.get('dim_labels', []))
            if not (fits(actual_type, expected) and fits(expected, actual_type)):
                raise ValueError('array view provider signature does not match declaration')
        if any(k != hook.input and v.get('required', True) for k, v in provider['inputs'].items()):
            raise ValueError('array view provider has additional required inputs')
        plan = dict(edge_id=edge.id, owner=owner, tag=tag, view=name,
                    definition=view.model_dump(mode='json'), direction=direction,
                    provider=hook.model_dump(mode='json'))
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:20]
        node_id = 'array-view-' + digest
        if node_id in nodes:
            raise ValueError('array view generated node ID collision')
        target = nodes[edge.target]
        new = GraphNode(id=node_id, algorithm_name=hook.name, algorithm_version=hook.version,
                        assigned_node_id=target.assigned_node_id)
        result.nodes.append(new)
        nodes[node_id] = new
        replacement = GraphEdge(id=edge.id + '-array-view-input', source=edge.source,
                                sourceHandle=edge.sourceHandle, target=node_id, targetHandle=hook.input)
        edge.source, edge.sourceHandle = node_id, hook.output
        result.edges.append(replacement)
        plan['node_id'] = node_id
        plans.append(plan)
        return node_id, hook.output

    # Each edge is planned after its producer's effective output and provenance
    # have been resolved; generated providers are not recursively adapted.
    for node_id in execution_topological_order(graph):
        node = nodes[node_id]
        typ = port_types(node)
        if typ is None:
            continue
        incoming = [e for e in result.edges if e.target == node_id]
        for edge in incoming:
            source = nodes[edge.source]
            source_types = port_types(source)
            if source_types is None or edge.sourceHandle not in source_types.outputs or edge.targetHandle not in typ.inputs:
                continue
            src = source_types.outputs[edge.sourceHandle].views.aggregate
            dst = typ.inputs[edge.targetHandle].views.aggregate
            labels = typ.inputs[edge.targetHandle].declaration.require_axis_label_match
            if fits(src, dst, labels):
                continue
            if isinstance(src, ObjectType) and isinstance(dst, ArrayType):
                candidates = [(owner, tag, name, view) for (tag, name), (owner, view) in offers.items()
                              if tag in src.tags and fits(from_legacy(view.array.tags, True, view.array.dim_labels), dst, labels)]
                if len(candidates) != 1:
                    raise ValueError(f'edge {edge.id}: array view selection requires one candidate, got {len(candidates)}')
                owner, tag, name, view = candidates[0]
                key = insert(edge, owner, tag, name, view, 'to_array')
                lineage[key] = candidates[0]
            elif isinstance(src, ArrayType) and isinstance(dst, ObjectType):
                origin = lineage.get((edge.source, edge.sourceHandle))
                if origin is None:
                    raise ValueError(f'edge {edge.id}: cannot restore array without view provenance')
                owner, tag, name, view = origin
                if not fits(ObjectType(tags=[tag]), dst):
                    raise ValueError(f'edge {edge.id}: restoration target differs from original type')
                insert(edge, owner, tag, name, view, 'from_array')
        # Only declared generic passthrough outputs propagate a view. Arbitrary
        # algorithm results of the same tag do not acquire trustworthy provenance.
        typ = port_types(node)
        pack = packs.get(package_key(node.algorithm_name, node.algorithm_version))
        for output, spec in pack.outputs.items():
            if not spec.tags_from:
                continue
            incoming_edges = [e for e in result.edges if e.target == node_id and e.targetHandle == spec.tags_from]
            if len(incoming_edges) != 1:
                continue
            origin = lineage.get((incoming_edges[0].source, incoming_edges[0].sourceHandle))
            if origin and isinstance(typ.outputs[output].views.aggregate, ArrayType):
                lineage[node_id, output] = origin
    # An unconsumed derived array is an explicit end of the array segment.
    # Restore it by an ordinary terminal provider; keep the intermediate array
    # inspectable. No operation-name lookup or per-operation implementation.
    for (source_id, source_port), origin in list(lineage.items()):
        if any(e.source == source_id and e.sourceHandle == source_port for e in result.edges):
            continue
        owner, tag, name, view = origin
        sentinel = GraphEdge(id=f"terminal-{source_id}-{source_port}", source=source_id,
                             sourceHandle=source_port, target=source_id, targetHandle=source_port)
        insert(sentinel, owner, tag, name, view, 'from_array')
    return result, plans
