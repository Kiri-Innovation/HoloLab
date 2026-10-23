"""Read-only workflow capability checks, also used by authoritative preparation."""

import asyncio
import uuid

from fastapi import HTTPException

from hololab.node.capabilities import BOUNDARY, enforce
from hololab.package_identity import offered_identity, package_key
from hololab.protocol.messages import CapabilityReq


async def check_workflow(app, graph, *, required=None, fresh=False, strict=False):
    from hololab.gateway.app import _await_node_reply

    catalog = {package_key(p["name"], p["version"]): p for p in app.state.registry.catalog_json()}
    limit = asyncio.Semaphore(8)

    async def check(node):
        session = app.state.registry.get_session(node.assigned_node_id)
        entry = catalog.get(package_key(node.algorithm_name, node.algorithm_version), {})
        if session is not None and getattr(session, "environment_checks", False):
            # Catalog combines machines. A selected node's own contract wins.
            matches = [
                p
                for p in session.packs
                if package_key(p.name, p.version)
                == package_key(node.algorithm_name, node.algorithm_version)
            ]
            if matches:
                entry = {**entry, "requires": matches[0].requires}
        declared = entry.get("requires") is not None
        report = {
            "status": "undeclared",
            "issues": [],
            "boundary": BOUNDARY,
            "environment_covered": False,
            "runtime_verified": False,
            "checked_at": None,
        }
        try:
            if not entry:
                raise ValueError("包声明不可用, 无法确认是否声明环境要求")
            if session is None:
                if declared:
                    raise ValueError("选择在线执行节点后重新检查")
                report["note"] = "未声明; 执行节点未选择或离线"
            elif not getattr(session, "environment_checks", False):
                if declared:
                    raise ValueError("节点不支持环境契约检查, 请升级节点")
            elif declared:
                name, version = offered_identity(
                    session.packs, node.algorithm_name, node.algorithm_version
                )
                request = CapabilityReq(
                    req_id=str(uuid.uuid4()),
                    algorithm_name=name,
                    algorithm_version=version,
                    fresh=fresh,
                )
                async with limit:
                    reply = await _await_node_reply(
                        app,
                        session,
                        "capability_req",
                        request,
                        request.req_id,
                        pending_key="pending_capabilities",
                        timeout_s=90,
                    )
                if reply.get("error") or not reply.get("report"):
                    raise ValueError(reply.get("error") or "节点未返回环境检查结果")
                report = reply["report"]
        except (ValueError, TimeoutError, ConnectionError, HTTPException) as exc:
            report.update(
                status="unknown",
                issues=[
                    {
                        "item": "capability_check",
                        "required": entry.get("requires"),
                        "actual": str(exc),
                        "remedy": "检查节点连接与版本后重新检查",
                    }
                ],
            )
        report = {
            **report,
            "node_id": node.assigned_node_id,
            "source": f"{node.algorithm_name}@{node.algorithm_version}",
        }
        if strict:
            try:
                enforce(report)
            except ValueError as exc:
                raise ValueError(f"{node.id} ({report['source']}): {exc}") from exc
        return node.id, report

    rows = await asyncio.gather(
        *(
            check(n)
            for n in graph.nodes
            if n.kind == "algorithm" and (required is None or n.id in required)
        )
    )
    return dict(rows)


def require_check_support(session, node):
    """A recorded contract must not silently run on an older node after reconnect."""
    recorded = (node.code_identity or {}).get("manifest", {}).get("normalized", {}).get("requires")
    declared = recorded is not None or any(
        getattr(p, "requires", None) is not None
        for p in session.packs
        if package_key(p.name, p.version)
        == package_key(node.algorithm_name, node.algorithm_version)
    )
    if declared and not getattr(session, "environment_checks", False):
        raise ValueError("节点不支持已声明的环境契约检查, 请升级节点后重新提交")
