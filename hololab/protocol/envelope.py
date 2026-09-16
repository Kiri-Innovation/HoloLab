"""Wire envelope: version + kind + payload + timestamp.

Every WebSocket frame is exactly one envelope encoded as JSON. Kind-specific
payloads are validated against the models in ``messages.py`` by
:func:`decode`, and rendered by :func:`encode`.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from hololab import PROTOCOL_V_MAX, PROTOCOL_V_MIN
from hololab.protocol import messages

# Discriminated union: kind string → payload model class.
# Adding a new message kind requires a matching entry here and, on breaking
# changes, a protocol version bump.
_KIND_REGISTRY: dict[str, type[BaseModel]] = {
    "register": messages.Register,
    "register_ok": messages.RegisterOk,
    "register_err": messages.RegisterErr,
    "heartbeat": messages.Heartbeat,
    "packs_updated": messages.PacksUpdated,
    "job_assign": messages.JobAssign,
    "job_ack": messages.JobAck,
    "job_progress": messages.JobProgress,
    "job_log": messages.JobLog,
    "job_done": messages.JobDone,
    "job_fail": messages.JobFail,
    "job_cancel": messages.JobCancel,
    "handle_register": messages.HandleRegister,
    "handle_locate_req": messages.HandleLocateReq,
    "handle_locate_resp": messages.HandleLocateResp,
    "job_update": messages.JobUpdate,
    "log_chunk": messages.LogChunk,
    "preview_ready": messages.PreviewReady,
    "node_online": messages.NodeOnline,
    "node_offline": messages.NodeOffline,
    "node_config_get_req": messages.NodeConfigGetReq,
    "node_config_get_resp": messages.NodeConfigGetResp,
    "node_config_set_req": messages.NodeConfigSetReq,
    "node_config_set_resp": messages.NodeConfigSetResp,
    "handle_check_req": messages.HandleCheckReq,
    "handle_check_resp": messages.HandleCheckResp,
    "artifact_delete_req": messages.ArtifactDeleteReq,
    "artifact_delete_resp": messages.ArtifactDeleteResp,
}


class Envelope(BaseModel):
    """Wire-level envelope for every WebSocket message.

    Attributes:
        v: Protocol version chosen at handshake time.
        id: Per-message UUID, useful for log correlation.
        kind: Discriminator for the payload model.
        payload: Kind-specific payload (validated by :func:`decode`).
        ts: Sender wall-clock timestamp (seconds since epoch).
    """

    v: int
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str
    payload: dict[str, Any]
    ts: float = Field(default_factory=time.time)


def encode(kind: str, payload: BaseModel | dict[str, Any], *, v: int = PROTOCOL_V_MAX) -> str:
    """Encode a message to a JSON string ready for ``ws.send``.

    Args:
        kind: The message kind (must exist in the kind registry).
        payload: Either a pydantic model instance or a plain dict.
        v: Protocol version to stamp the envelope with.

    Raises:
        KeyError: if ``kind`` is not registered.
        ValueError: if ``payload`` is a model that doesn't match ``kind``.
    """

    expected = _KIND_REGISTRY[kind]
    if isinstance(payload, BaseModel):
        if not isinstance(payload, expected):
            raise ValueError(
                f"payload type {type(payload).__name__} does not match kind={kind!r} "
                f"(expected {expected.__name__})"
            )
        payload_dict = payload.model_dump(mode="json")
    else:
        # Validate before wrapping — reject early if the caller passed junk.
        expected.model_validate(payload)
        payload_dict = dict(payload)

    envelope = Envelope(v=v, kind=kind, payload=payload_dict)
    return envelope.model_dump_json()


def decode(raw: str | bytes) -> tuple[Envelope, BaseModel]:
    """Parse a wire frame into an envelope and its typed payload model.

    Returns:
        A tuple ``(envelope, payload_model)``.

    Raises:
        ValueError: on malformed JSON, unknown kind, or payload validation error.
    """

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"envelope is not valid JSON: {exc}") from exc

    envelope = Envelope.model_validate(data)

    if envelope.kind not in _KIND_REGISTRY:
        raise ValueError(f"unknown message kind: {envelope.kind!r}")

    model_cls = _KIND_REGISTRY[envelope.kind]
    payload_model = model_cls.model_validate(envelope.payload)
    return envelope, payload_model


def negotiate_version(peer_v_min: int, peer_v_max: int) -> int:
    """Choose the highest protocol version supported by both peers.

    Args:
        peer_v_min: The peer's declared minimum.
        peer_v_max: The peer's declared maximum.

    Returns:
        The chosen version.

    Raises:
        ValueError: if there is no overlap with our supported range.
    """

    low = max(PROTOCOL_V_MIN, peer_v_min)
    high = min(PROTOCOL_V_MAX, peer_v_max)
    if low > high:
        raise ValueError(
            f"no compatible protocol version: peer supports [{peer_v_min}, {peer_v_max}], "
            f"we support [{PROTOCOL_V_MIN}, {PROTOCOL_V_MAX}]"
        )
    return high
