"""In-memory rolling metrics buffers, one ring per compute node.

The gateway ingests ``node_metrics`` frames from every connected
node and buffers a modest fine-grained history so the frontend's
"server pulse" panel (rendered when the canvas selection is empty)
can chart CPU / memory / GPU utilisation + VRAM without polling any
node directly.

Storage policy for the MVP:
    * Buffer size is :data:`DEFAULT_BUFFER_SIZE` samples per node
      (~1 hour at the daemon's 3 s sampling cadence). That is enough
      to see "is this box busy right now" and to catch a spike from
      ten minutes ago on the sparkline.
    * Nothing is persisted to SQLite. On a gateway restart the buffer
      re-fills in under a minute of live traffic; if the user needs
      longer archives, a rollup table + periodic flush is the next
      step — see :func:`downsampled_summary` for the shape we would
      eventually expose.
    * Coarser tiers (6 h / 24 h at 1-min resolution) are deliberately
      out of scope here. The panel can add them later without a new
      wire format: the sample shape is the same, the ring just grows.

The store is process-local; each ``FastAPI`` app owns exactly one and
attaches it to ``app.state.metrics`` at startup.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from hololab.protocol.messages import NodeMetrics

# One sample every ~3 s — matches ``METRICS_INTERVAL_SECONDS`` in
# ``hololab.node.metrics``. Exposed here so the REST endpoint can tell
# the frontend the sparkline x-axis spacing without a magic number.
DEFAULT_SAMPLE_INTERVAL_S = 3.0

# 1200 samples at 3 s per sample is approximately one hour. Chosen slightly larger so a
# handful of missed samples during network hiccups don't visibly
# shorten the timeline.
DEFAULT_BUFFER_SIZE = 1200


class MetricsStore:
    """Per-node ring buffers of :class:`NodeMetrics` samples."""

    def __init__(self, *, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self._by_node: dict[str, deque[NodeMetrics]] = {}
        self._buffer_size = buffer_size

    def record(self, node_id: str, sample: NodeMetrics) -> NodeMetrics:
        """Append a sample; return the copy stamped with ``node_id``.

        Nodes leave ``node_id`` unset on the wire — the gateway is the
        authority for identity and re-stamps here so both the buffered
        history and the frontend rebroadcast agree on the field.
        """

        stamped = sample.model_copy(update={"node_id": node_id})
        buf = self._by_node.get(node_id)
        if buf is None:
            buf = deque(maxlen=self._buffer_size)
            self._by_node[node_id] = buf
        buf.append(stamped)
        return stamped

    def history(self, node_id: str, *, since: float | None = None) -> list[NodeMetrics]:
        """Return the retained samples for one node, oldest first."""

        buf = self._by_node.get(node_id)
        if buf is None:
            return []
        if since is None:
            return list(buf)
        return [s for s in buf if s.ts > since]

    def all_history(self, *, since: float | None = None) -> dict[str, list[NodeMetrics]]:
        """Snapshot every buffered node's history in one pass."""

        return {nid: self.history(nid, since=since) for nid in self._by_node}

    def drop(self, node_id: str) -> None:
        """Forget the buffer for a node.

        The gateway currently does NOT call this on offline — keeping
        the buffer means the operator can see the last known values
        while a node is reconnecting after a hiccup. Exposed for tests
        and future policy tweaks.
        """

        self._by_node.pop(node_id, None)


def metrics_to_dict(sample: NodeMetrics) -> dict[str, Any]:
    """JSON-serialisable form for the REST history endpoint."""

    return sample.model_dump(mode="json")
