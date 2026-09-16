"""Jinja2 template rendering for manifest shell commands and paths.

Manifest strings that support templating: ``exec.shell``, ``exec.working_dir``,
``idempotency.marker``, ``previews[].path``, ``previews[].glob``,
``progress.status_file``.

Available bindings (see docs/pack-spec.md#exec):
    inputs.<name>       -> absolute path to input
    outputs.<name>      -> absolute path to output directory
    params.<name>       -> user-supplied param value
    pack_dir            -> absolute path to the pack directory
    workspace_root      -> absolute path to node's workspace root
    scratch_dir         -> per-job scratch directory, materialised under
                           ``{workspace_root}/scratch/{job_id}/`` before
                           the shell runs. Use for intermediate files
                           that don't need to survive the job; never use
                           ``mktemp`` / ``/tmp`` for large artifacts
                           (system disk fills up silently — see the
                           Phase-0 mono-smoke incident).
    job_id, workflow_id -> ids for logging
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError

from hololab.manifest.schema import Manifest


@dataclass
class RenderContext:
    """All templating bindings for one job run."""

    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    pack_dir: str = ""
    workspace_root: str = ""
    # Per-job intermediate directory. Node runtime resolves this to
    # ``{workspace_root}/scratch/{job_id}/`` and ``mkdir -p``s it before
    # dispatching the subprocess. Kept after the job for post-mortem
    # inspection; the Artifacts page manages cleanup.
    scratch_dir: str = ""
    job_id: str = ""
    workflow_id: str = ""

    def to_bindings(self) -> dict[str, Any]:
        """Flatten to Jinja2 context (nested dicts for dot access)."""

        return {
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "params": dict(self.params),
            "pack_dir": self.pack_dir,
            "workspace_root": self.workspace_root,
            "scratch_dir": self.scratch_dir,
            "job_id": self.job_id,
            "workflow_id": self.workflow_id,
        }


# ``StrictUndefined`` turns typos into loud errors ("params.foo is undefined")
# rather than silently rendering empty strings.
_JINJA = Environment(
    undefined=StrictUndefined,
    keep_trailing_newline=False,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render_str(template: str, ctx: dict[str, Any], *, where: str) -> str:
    try:
        return _JINJA.from_string(template).render(**ctx)
    except TemplateError as exc:
        raise ValueError(f"template render error at {where}: {exc}") from exc


@dataclass
class RenderedManifest:
    """Concrete strings ready to hand to a subprocess."""

    shell: str
    working_dir: str | None
    idempotency_marker: str | None
    preview_paths: dict[str, str]  # preview_id → path (empty for glob-only)
    preview_globs: dict[str, str]  # preview_id → glob (empty for path-only)
    progress_status_file: str | None


def render_manifest(manifest: Manifest, ctx: RenderContext) -> RenderedManifest:
    """Render all templatable strings of a manifest against a context."""

    bindings = ctx.to_bindings()

    shell = _render_str(manifest.exec.shell, bindings, where="exec.shell")
    working_dir = (
        _render_str(manifest.exec.working_dir, bindings, where="exec.working_dir")
        if manifest.exec.working_dir
        else None
    )

    idempotency_marker = None
    if manifest.idempotency is not None:
        idempotency_marker = _render_str(
            manifest.idempotency.marker, bindings, where="idempotency.marker"
        )

    preview_paths: dict[str, str] = {}
    preview_globs: dict[str, str] = {}
    for pv in manifest.previews:
        if pv.path:
            preview_paths[pv.id] = _render_str(pv.path, bindings, where=f"previews[{pv.id}].path")
        if pv.glob:
            preview_globs[pv.id] = _render_str(pv.glob, bindings, where=f"previews[{pv.id}].glob")

    progress_status_file = None
    if manifest.progress and manifest.progress.status_file:
        progress_status_file = _render_str(
            manifest.progress.status_file, bindings, where="progress.status_file"
        )

    return RenderedManifest(
        shell=shell,
        working_dir=working_dir,
        idempotency_marker=idempotency_marker,
        preview_paths=preview_paths,
        preview_globs=preview_globs,
        progress_status_file=progress_status_file,
    )


def rendered_output_paths(
    manifest: Manifest, workspace_root: Path, workflow_id: str, job_id: str
) -> dict[str, str]:
    """Compute the conventional output directory for each declared output.

    The convention is ``{workspace_root}/w/{workflow_id}/j/{job_id}/{output_name}/``.
    Node materializes these before executing the command; the manifest sees
    them via ``outputs.<name>``.
    """

    base = workspace_root / "w" / workflow_id / "j" / job_id
    return {name: str(base / name) for name in manifest.outputs}


def rendered_scratch_dir(workspace_root: Path, job_id: str) -> str:
    """Per-job scratch directory path — canonical location.

    Kept flat (``scratch/{job_id}``) rather than nested under
    ``w/{workflow_id}/`` so cleanup can iterate a single directory
    without having to walk the workflow tree. Every job's intermediate
    data — including ad-hoc / adhoc- workflow ids — lands in the same
    parent, which the Artifacts page uses to bulk-clean.
    """

    return str(workspace_root / "scratch" / job_id)
