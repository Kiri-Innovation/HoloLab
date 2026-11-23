"""Read-only workflow capability checks, also used by authoritative preparation."""

import asyncio
import uuid

from fastapi import HTTPException

from hololab.node.capabilities import BOUNDARY, enforce
from hololab.package_identity import offered_identity, package_key
from hololab.protocol.messages import CapabilityReq, EnvironmentEvidenceEntry


async def check_workflow(app, graph, *, required=None, fresh=False, strict=False):
    from hololab.gateway.app import _await_node_reply

    catalog = {package_key(p["name"], p["version"]): p for p in app.state.registry.catalog_json()}
    limit = asyncio.Semaphore(8)
    check_batch_id = str(uuid.uuid4())

    async def check(node):
        session = app.state.registry.get_session(node.assigned_node_id)
        entry = catalog.get(package_key(node.algorithm_name, node.algorithm_version), {})
        matches = (
            [
                pack
                for pack in session.packs
                if package_key(pack.name, pack.version)
                == package_key(node.algorithm_name, node.algorithm_version)
            ]
            if session is not None
            else []
        )
        offered_pack = matches[0] if len(matches) == 1 else None
        if offered_pack is not None and getattr(session, "environment_checks", False):
            # Catalog combines machines. A selected node's own contract wins.
            entry = {**entry, "requires": offered_pack.requires}
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
            if session is not None and offered_pack is None:
                report.update(
                    status="missing",
                    issues=[
                        {
                            "item": "algorithm_package",
                            "required": f"{node.algorithm_name}@{node.algorithm_version}",
                            "actual": "执行节点提供的版本有歧义"
                            if matches
                            else "执行节点未提供此版本",
                            "remedy": "在该节点安装此确切版本，或将工作流改为已提供的版本",  # noqa: RUF001
                        }
                    ],
                )
            elif not entry:
                raise ValueError("包声明不可用, 无法确认是否声明环境要求")
            elif session is None:
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
                    check_batch_id=check_batch_id,
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
                if app.state.registry.get_session(session.node_id) is not session:
                    raise ValueError("节点会话已变化, 请重新检查")
                if offered_pack is not None and offered_pack.environment_inventory_id is not None:
                    inventory_id = reply.get("environment_inventory_id")
                    if inventory_id != offered_pack.environment_inventory_id or not any(
                        p.name == name
                        and p.version == version
                        and p.environment_inventory_id == inventory_id
                        and p.manifest_hash == offered_pack.manifest_hash
                        and p.code_sha256 == offered_pack.code_sha256
                        for p in session.packs
                    ):
                        raise ValueError("包清单身份已变化, 请重新检查")
                    await app.state.registry.replace_environment_evidence(
                        session.node_id,
                        [
                            EnvironmentEvidenceEntry(
                                name=name,
                                version=version,
                                report=reply["report"],
                                environment_inventory_id=inventory_id,
                            )
                        ],
                        expected_session=session,
                    )
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
        return node.id, report

    rows = await asyncio.gather(
        *(
            check(n)
            for n in graph.nodes
            if n.kind == "algorithm" and (required is None or n.id in required)
        )
    )
    reports = dict(rows)
    if strict:
        enforce_workflow(reports)
    return reports


def environment_errors(reports):
    errors = []
    for node_id, report in reports.items():
        try:
            enforce(report)
        except ValueError as exc:
            errors.append(f"{node_id} ({report['source']}): {exc}")
    return errors


def enforce_workflow(reports):
    errors = environment_errors(reports)
    if errors:
        raise ValueError("\n".join(errors))


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
