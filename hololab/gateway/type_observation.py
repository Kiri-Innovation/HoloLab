"""Runtime evidence envelope. Contract dimensions never manufacture measurements."""

from typing import Literal

from hololab.gateway.port_type_ir import (
    CompleteEvidence,
    InternalCount,
    Model,
    Observation,
    ObservedAxis,
    RuntimeShape,
    SampledEvidence,
    UnknownEvidence,
)


class InternalCountItem(Model):
    """Typed legacy projection, retaining the exact label/value wire keys."""

    label: str
    value: int


class RuntimeObservation(RuntimeShape):
    schemaVersion: Literal[1] = 1


def measurement(value, *, source: str, sample: str | None = None):
    if sample is not None:
        return Observation(evidence=SampledEvidence(value=value, source=source, sample=sample))
    return Observation(evidence=CompleteEvidence(value=value, source=source))


def observe_summary(
    *,
    handle_id: str,
    tags: list[str],
    facts: dict,
    labels: list[str] | None,
    content_depth: int,
    sample: str | None,
    axis_sample: str | None,
    accessible: bool,
) -> RuntimeObservation:
    """Attach scope at the probe boundary, never infer coverage from old JSON.

    Directory branching factors are scanned across the complete tree. Intrinsic
    content dimensions and counts at one child are samples of the parent, even
    if their numbers happen to match all other children. No absolute paths leak.
    """
    axes = Observation(evidence=UnknownEvidence(reason="shape-not-measured"))
    sizes = facts.get("dim_sizes")
    if sizes is not None:
        directory_depth = max(0, len(sizes) - content_depth)
        observed = []
        for i, size in enumerate(sizes):
            is_content = i >= directory_depth
            observed.append(
                ObservedAxis(
                    label=labels[i] if labels and i < len(labels) else None,
                    role="content" if is_content else "collection",
                    size=measurement(
                        size,
                        source="tag-content-probe" if is_content else "directory-tree-scan",
                        sample=axis_sample if is_content else None,
                    ),
                )
            )
        axes = measurement(
            observed, source="handle-shape-probe", sample=axis_sample if content_depth else None
        )
    counts = [
        InternalCount(
            key=item["label"],
            count=measurement(item["value"], source="tag-internal-count-probe", sample=sample),
        )
        for item in facts.get("internal_count_items", [])
    ]
    if facts.get("internal_count") is not None:
        counts.append(
            InternalCount(
                key="",
                count=measurement(
                    facts["internal_count"], source="tag-internal-count-probe", sample=sample
                ),
            )
        )
    if not accessible:
        axes = Observation(evidence=UnknownEvidence(reason="handle-unavailable"))
        counts = []
    return RuntimeObservation(
        handleId=handle_id,
        tags=measurement(tags, source="registered-handle-metadata"),
        axes=axes,
        internalCounts=counts,
        legacyCountKind=facts.get("internal_count_kind"),
    )
