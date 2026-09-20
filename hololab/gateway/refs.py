"""Copy-paste resource references — the ``hololab://`` scheme.

Motivation: when a user points at "this thing" in the browser UI and
wants an agent to resolve it, we need a single-line token that (a) a
human can visually recognise + paste into a chat, (b) an agent can
resolve unambiguously with one API call, no guessing.

Design:

    hololab://<kind>/<id>[  # human-readable comment]

    kinds: workflow | run | job | handle | pack | node | graph-node
    id:    UUID for id-shaped kinds; ``name@version`` for ``pack``;
           ``<workflow_uuid>/<graph_node_id>`` compound for
           ``graph-node`` (since a graph node id is only unique inside
           its workflow).

The four object families that appear on the canvas are semantically
distinct — agents should reason about them as such:

    workflow    — a graph definition (draft), stable across runs
    run         — one lineage snapshot; snapshots may share jobs
    graph-node  — a POSITION in a workflow's graph (algorithm slot),
                  stable across runs, timeline-independent
    job         — one execution event (a graph-node run once)
    handle      — one produced artifact (data)

The single ``⧉`` copy button on any UI element emits the ref for the
kind of thing that button represents:

    canvas node card    → graph-node (position, most stable)
    recent-jobs row     → job        (execution, one-of-many)
    preview drawer      → handle     (artifact, the actual bytes)

Nesting is derivable — resolving a graph-node ref returns the latest
job / snapshot / handles at that position; the agent doesn't have to
guess the ref format for descendants.

The comment tail (``# ...``) is optional — the UI's copy button appends
it for human readability (e.g. algorithm name, run timestamp, state)
and the parser strips it before matching. This lets a paste like

    hololab://job/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b  # stg-train · done

round-trip losslessly through the resolver.

The scheme deliberately does NOT encode the host — the agent runs
against whatever HoloLab it's connected to (``$HOLO/api/resolve?ref=...``)
so hard-coding ``http://127.0.0.1:8828`` in the token would confuse the
cross-machine case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCHEME_PREFIX = "hololab://"
_ALLOWED_KINDS = frozenset(
    (
        "workflow",
        "run",
        "job",
        "handle",
        "pack",
        "node",
        "graph-node",
        "workflows",
        "artifacts",
    )
)
# Kinds that name a page-level *index* rather than a single resource.
# They carry no id — the reference is ``hololab://<kind>`` on its own
# (with an optional ``  # comment`` tail). Adding one here + a resolve
# handler in ``gateway.app`` is enough to plumb another "collection"
# page through the ref scheme.
_INDEX_KINDS = frozenset(("workflows", "artifacts"))

# UUID for the id-shaped kinds. Loose enough to accept both dashed and
# undashed forms; strict enough to reject obvious garbage.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Pack spec ``name@version`` — same rules as manifest names (lowercase
# alphanum + dash/underscore + digits/dots).
_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*@\d+\.\d+\.\d+$")
# Graph node id — same allowed alphabet as manifest names, but the
# whole thing lives after ``<workflow_uuid>/``. We accept a slightly
# broader charset (mixed case + digits) since graph node ids are
# frontend-minted and don't have to be lowercase.
_GRAPH_NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class RefParseError(ValueError):
    """Raised when a copied token doesn't match the scheme.

    The endpoint converts this into a 400 with the message text — the
    error text is user-facing, so keep it actionable ("expected
    ``hololab://<kind>/<id>``, got …").
    """


@dataclass(frozen=True)
class Ref:
    """One parsed reference. ``comment`` preserves the ``# ...`` tail
    (without the ``#``) so a caller can echo it back to the user in a
    confirmation UI, but it plays no role in resolution."""

    kind: str
    id: str
    comment: str | None = None

    def canonical(self) -> str:
        """The parseable ``hololab://<kind>[/<id>]`` form without comment.

        Index kinds (``workflows`` / ``artifacts``) render without the
        trailing ``/id`` segment; every other kind carries the id.
        """

        if self.kind in _INDEX_KINDS:
            return f"{_SCHEME_PREFIX}{self.kind}"
        return f"{_SCHEME_PREFIX}{self.kind}/{self.id}"


def parse_ref(raw: str) -> Ref:
    """Parse a ``hololab://`` token into a :class:`Ref`.

    Accepts an optional ``  # <comment>`` tail — the UI's copy button
    emits this for readability, the resolver ignores it. Two forms of
    comment separation are recognised: an unescaped ``#`` preceded by
    whitespace, or the literal string ``  #`` anywhere in the tail.

    Raises :class:`RefParseError` on any malformed input; the error
    message is safe to surface to a user.
    """

    text = raw.strip()
    if not text:
        raise RefParseError("empty reference")

    # Split off the comment tail if present. We look for ``#`` preceded
    # by whitespace so a UUID like ``…#…`` (never real, but defensive)
    # doesn't get chopped mid-body.
    comment: str | None = None
    hash_split = re.search(r"\s+#\s?(.*)$", text)
    if hash_split is not None:
        comment = hash_split.group(1).strip() or None
        text = text[: hash_split.start()].strip()

    if not text.startswith(_SCHEME_PREFIX):
        raise RefParseError(f"reference must start with {_SCHEME_PREFIX!r}, got {text!r}")

    body = text[len(_SCHEME_PREFIX) :]

    # Index-kind refs (``hololab://workflows``, ``hololab://artifacts``)
    # carry no id segment at all. Split-on-``/`` handles both the
    # id-carrying kinds and the trailing-slash-tolerant forms of index
    # kinds (``hololab://workflows/`` is treated as an empty id and
    # falls into the same branch).
    if "/" not in body:
        kind = body.strip().lower()
        ident = ""
    else:
        kind, _, ident = body.partition("/")
        kind = kind.strip().lower()
        ident = ident.strip()

    if kind not in _ALLOWED_KINDS:
        raise RefParseError(
            f"unknown reference kind {kind!r} (allowed: {', '.join(sorted(_ALLOWED_KINDS))})"
        )

    if kind in _INDEX_KINDS:
        if ident:
            raise RefParseError(
                f"reference kind {kind!r} is an index and must not carry an id, got {text!r}"
            )
        return Ref(kind=kind, id="", comment=comment)

    if not ident:
        raise RefParseError(f"reference kind {kind!r} needs an id")

    _validate_id(kind, ident)
    return Ref(kind=kind, id=ident, comment=comment)


def format_ref(kind: str, ident: str, comment: str | None = None) -> str:
    """Emit a token string. Used by the frontend `CopyRefButton`.

    Not strictly server-side (the frontend does its own emission), but
    kept here as the single source of truth for the scheme so a test
    can round-trip parse(format(...)).
    """

    if kind not in _ALLOWED_KINDS:
        raise RefParseError(f"unknown kind {kind!r}")
    if kind in _INDEX_KINDS:
        if ident:
            raise RefParseError(f"index kind {kind!r} must not carry an id, got {ident!r}")
        base = f"{_SCHEME_PREFIX}{kind}"
    else:
        _validate_id(kind, ident)
        base = f"{_SCHEME_PREFIX}{kind}/{ident}"
    if comment:
        # Two-space gap before ``#`` so the parser recognises the tail.
        return f"{base}  # {comment}"
    return base


def split_graph_node_id(ident: str) -> tuple[str, str]:
    """Split a ``graph-node`` compound id into (workflow_id, graph_node_id).

    Raises :class:`RefParseError` if either half doesn't validate. Kept
    separate so the resolver + frontend can share the split without
    re-implementing it.
    """

    if "/" not in ident:
        raise RefParseError(f"graph-node id must be <workflow_uuid>/<graph_node_id>, got {ident!r}")
    wid, _, gnid = ident.partition("/")
    if not _UUID_RE.match(wid):
        raise RefParseError(f"graph-node workflow id must be a UUID, got {wid!r}")
    if not _GRAPH_NODE_ID_RE.match(gnid):
        raise RefParseError(
            f"graph-node id segment {gnid!r} must match {_GRAPH_NODE_ID_RE.pattern!r}"
        )
    return wid, gnid


def _validate_id(kind: str, ident: str) -> None:
    if kind == "pack":
        if not _PACK_ID_RE.match(ident):
            raise RefParseError(f"pack id must be name@version, got {ident!r}")
        return
    if kind == "graph-node":
        # Compound id — delegates to the shared splitter so we don't
        # drift from the resolver's parsing.
        split_graph_node_id(ident)
        return
    # All other kinds are UUIDs.
    if not _UUID_RE.match(ident):
        raise RefParseError(f"{kind} id must be a UUID, got {ident!r}")
