"""Prepare conda environments declared by installed algorithm packs.

This deliberately lives outside the node daemon: changing an environment is an
operator action, and a running node must be restarted to refresh its env cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from hololab.content_scope import Baselines, capture
from hololab.manifest import load_manifest
from hololab.node.config import NodeConfig
from hololab.pack_install import receipt_for
from hololab.package_identity import canonical_name, parse_spec


class SetupError(ValueError):
    """A safe-to-display setup failure."""


# Python 3.11 is the conservative common denominator for the scientific and
# computer-vision packages algorithm packs commonly use.  Keep this list in
# preference order rather than asking conda to choose an unconstrained latest
# interpreter.  Later entries allow an author constraint to be honoured when
# it excludes the default.
DEFAULT_PYTHON = "3.11"
_PYTHON_CANDIDATES = ("3.11", "3.12", "3.10", "3.9", "3.8", "3.7", "3.6", "3.13", "3.14", "3.15")
_ECOSYSTEM_PYTHONS = frozenset({"3.10", "3.11", "3.12", "3.13"})


@dataclass(frozen=True)
class EnvironmentPlan:
    name: str
    packages: tuple[str, ...]
    python: str | None
    packs: tuple[str, ...]
    prefix: Path | None
    hooks: tuple[tuple[object, Path], ...] = ()


def select_python_version(requirement: str | None) -> str:
    """Choose an ecosystem-compatible minor version satisfying *requirement*.

    Broad declarations such as ``>=3.6`` intentionally select 3.11 instead
    of allowing conda to resolve a newly released interpreter.
    """
    if not requirement:
        return DEFAULT_PYTHON
    try:
        specifier = SpecifierSet(requirement)
    except InvalidSpecifier as exc:
        raise SetupError(f"invalid Python requirement {requirement!r}: {exc}") from exc
    for candidate in _PYTHON_CANDIDATES:
        if Version(candidate) in specifier:
            return candidate
    raise SetupError(
        f"Python requirement {requirement!r} has no supported selectable minor version"
    )


def find_conda() -> str | None:
    """Find a user-installed conda without downloading or installing one."""
    candidates = [shutil.which("conda"), os.environ.get("CONDA_EXE")]
    candidates.extend(str(Path.home() / p) for p in ("miniconda3/bin/conda", "anaconda3/bin/conda"))
    candidates.append("/opt/conda/bin/conda")
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).expanduser().resolve())
    return None


def installed_plans(cfg: NodeConfig, spec: str | None, all_packs: bool) -> list[EnvironmentPlan]:
    """Read installed manifests and merge their requirements by logical env."""
    if bool(spec) == bool(all_packs):
        raise SetupError("specify exactly one pack spec or --all")

    manifests: list[tuple[object, Path]] = []
    wanted: tuple[str, str] | None = None
    if spec:
        try:
            name, version = parse_spec(spec)
            wanted = (canonical_name(name), version)
        except ValueError as exc:
            raise SetupError(f"invalid pack spec: {exc}") from exc
    seen: set[tuple[str, str]] = set()
    for root in cfg.pack_dirs:
        if not root.exists():
            continue
        for path in root.rglob("manifest.yaml"):
            try:
                manifest, _ = load_manifest(path)
            except (OSError, ValueError):
                continue
            identity = (canonical_name(manifest.name), manifest.version)
            if identity in seen:
                continue
            seen.add(identity)
            if wanted and identity != wanted:
                continue
            manifests.append((manifest, path))
    if wanted and not manifests:
        raise SetupError(f"installed pack not found: {wanted[0]}@{wanted[1]}")

    grouped: dict[str, list[tuple[object, Path]]] = defaultdict(list)
    for manifest, _path in manifests:
        if manifest.runtime.mode == "host":
            continue
        grouped[manifest.runtime.env].append((manifest, _path))

    plans: list[EnvironmentPlan] = []
    for env, group_with_paths in sorted(grouped.items()):
        group = [item[0] for item in group_with_paths]
        python_values = {m.requires.python for m in group if m.requires and m.requires.python}
        if len(python_values) > 1:
            raise SetupError(
                f"conflicting Python requirements for environment {env!r}: "
                + ", ".join(sorted(python_values))
            )
        requested: dict[str, Requirement] = {}
        for manifest in group:
            for raw in manifest.requires.packages if manifest.requires else []:
                requirement = Requirement(raw)
                previous = requested.get(requirement.name.lower())
                if previous and previous.specifier != requirement.specifier:
                    # Two different constraints may be compatible, but guessing an
                    # intersection would conceal author errors. Make it explicit.
                    raise SetupError(
                        f"conflicting package requirements for environment {env!r}: "
                        f"{previous} vs {requirement}"
                    )
                requested[requirement.name.lower()] = requirement
        plans.append(
            EnvironmentPlan(
                env,
                tuple(str(x) for x in requested.values()),
                next(iter(python_values), None),
                tuple(sorted(f"{canonical_name(m.name)}@{m.version}" for m in group)),
                Path(cfg.envs[env]).expanduser() if env in cfg.envs else None,
                tuple(group_with_paths),
            )
        )
    return plans


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _prefix_for_name(conda: str, name: str) -> Path:
    result = _run([conda, "env", "list", "--json"])
    if result.returncode:
        raise SetupError(
            f"conda created/found {name!r} but could not list environments: {result.stderr.strip()}"
        )
    try:
        prefixes = json.loads(result.stdout)["envs"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SetupError("conda returned an invalid environment list") from exc
    for prefix in prefixes:
        if Path(prefix).name == name:
            return Path(prefix).resolve()
    raise SetupError(f"conda created/found {name!r} but did not report its prefix")


def _installed_version(conda: str, prefix: Path, package: str) -> Version | None:
    result = _run([conda, "list", "--prefix", str(prefix), "--json", package])
    if result.returncode:
        return None
    try:
        rows = json.loads(result.stdout)
        if rows:
            return Version(rows[0]["version"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return None


def _satisfied(conda: str, prefix: Path, raw: str) -> bool:
    requirement = Requirement(raw)
    version = _installed_version(conda, prefix, requirement.name)
    return version is not None and (not requirement.specifier or version in requirement.specifier)


def _environment_python_version(conda: str, prefix: Path) -> Version | None:
    result = _run([conda, "run", "--prefix", str(prefix), "python", "--version"])
    if result.returncode:
        return None
    value = (result.stdout or result.stderr).strip().removeprefix("Python ")
    try:
        return Version(value)
    except ValueError:
        return None


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_in_env(conda: str, prefix: Path, command: list[str], *, env: dict[str, str] | None = None):
    # Keep this separately injectable from _run: setup needs its deliberately
    # constrained context, while the older package installer needs no env.
    return subprocess.run(
        [conda, "run", "--prefix", str(prefix), *command],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _package_root(manifest: object, manifest_path: Path) -> Path:
    root = (manifest_path.parent / manifest.root).resolve()
    if not root.is_relative_to(manifest_path.parent.parent.resolve()) and manifest.root == ".":
        raise SetupError("package root escapes installed content")
    return root


def _verified_code_hash(manifest_path: Path) -> str:
    """Recompute code identity before executing untrusted pack author code."""
    receipt = receipt_for(manifest_path)
    with __import__("tempfile").TemporaryDirectory(prefix="hololab-setup-baseline-") as temp:
        record = capture(manifest_path, Baselines(Path(temp)), refresh_cold=True)
    if receipt and record["sha256"] != receipt["release"]["code_sha256"]:
        raise SetupError("archive/code hash verification failed; refusing to execute pack setup")
    return record["sha256"]


def _fingerprint(conda: str, prefix: Path, code_hash: str, script_hash: str) -> dict[str, str]:
    probe = _run_in_env(
        conda,
        prefix,
        [
            "python",
            "-c",
            "import sys, torch; print(sys.version.split()[0]); print(torch.__version__); print(torch.version.cuda or '')",
        ],
    )
    if probe.returncode:
        raise SetupError(
            "setup prerequisite missing: Python or PyTorch is unavailable in the target environment"
        )
    lines = probe.stdout.splitlines()
    nvcc = shutil.which("nvcc")
    nvcc_value = (
        subprocess.run([nvcc, "--version"], text=True, capture_output=True).stdout.strip()
        if nvcc
        else ""
    )
    return {
        "code_sha256": code_hash,
        "script_sha256": script_hash,
        "python": lines[0] if lines else "",
        "torch": lines[1] if len(lines) > 1 else "",
        "cuda_runtime": lines[2] if len(lines) > 2 else "",
        "nvcc": nvcc_value,
        "platform": platform.platform(),
    }


def _check_hook_needs(conda: str, prefix: Path, setup: object) -> None:
    # Python/torch/CUDA runtime are always relevant to native CUDA hooks.
    if "cuda-toolkit" in setup.needs and not shutil.which("nvcc"):
        raise SetupError(
            "setup prerequisite missing: CUDA compiler (nvcc). Install a CUDA toolkit and retry."
        )
    if "cxx-compiler" in setup.needs and not (
        shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    ):
        raise SetupError(
            "setup prerequisite missing: C++ compiler. Install g++ or clang++ and retry."
        )
    if "cuda-toolkit" in setup.needs:
        check = _run_in_env(
            conda,
            prefix,
            [
                "python",
                "-c",
                "import torch; assert torch.cuda.is_available(), 'CUDA runtime/GPU unavailable'; print(torch.cuda.get_device_capability())",
            ],
        )
        if check.returncode:
            raise SetupError(
                "setup prerequisite missing: CUDA runtime or usable GPU: " + check.stderr.strip()
            )


def _apply_hooks(
    plan: EnvironmentPlan, conda: str, prefix: Path, events: list[str], dry_run: bool
) -> None:
    for manifest, manifest_path in plan.hooks:
        setup = manifest.setup
        if setup is None:
            continue
        root = _package_root(manifest, manifest_path)
        script = (root / setup.script).resolve()
        if not script.is_file() or not script.is_relative_to(root):
            raise SetupError(f"setup script is missing or escapes package root: {setup.script}")
        if dry_run:
            events.append(
                f"{manifest.name}@{manifest.version}: run verified setup hook {setup.script}"
            )
            continue
        code_hash = _verified_code_hash(manifest_path)
        script_hash = _hash(script)
        _check_hook_needs(conda, prefix, setup)
        fingerprint = _fingerprint(conda, prefix, code_hash, script_hash)
        # Receipts are mutable operator state, never part of the verified pack.
        receipt = (
            prefix
            / ".hololab-pack-setups"
            / f"{canonical_name(manifest.name).replace('/', '__')}@{manifest.version}.json"
        )
        if receipt.is_file():
            try:
                old = json.loads(receipt.read_text())
            except json.JSONDecodeError:
                old = {}
            if old.get("fingerprint") == fingerprint and old.get("success") is True:
                events.append(
                    f"{manifest.name}@{manifest.version}: setup hook already matches fingerprint"
                )
                continue
        context = os.environ.copy()
        context.update(
            {
                "PACKAGE_ROOT": str(root),
                "CONDA_PREFIX": str(prefix),
                "PYTHON": str(prefix / "bin" / "python"),
                "PIP": str(prefix / "bin" / "pip"),
                "CUDA_HOME": os.environ.get(
                    "CUDA_HOME", str(Path(shutil.which("nvcc") or "/usr/local/cuda").parent.parent)
                ),
                "PACK_CODE_SHA256": code_hash,
            }
        )
        result = _run_in_env(conda, prefix, [setup.shell, str(script)], env=context)
        if result.returncode:
            tail = (result.stderr or result.stdout).strip()[-4000:]
            raise SetupError(
                f"setup hook failed for {manifest.name}@{manifest.version} (exit {result.returncode}):\n{tail}"
            )
        for module in setup.modules:
            smoke = _run_in_env(conda, prefix, ["python", "-c", f"import {module}"])
            if smoke.returncode:
                raise SetupError(
                    f"setup smoke test failed importing {module}: {smoke.stderr.strip()}"
                )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps(
                {"success": True, "fingerprint": fingerprint, "modules": setup.modules}, indent=2
            )
            + "\n"
        )
        events.append(f"{manifest.name}@{manifest.version}: setup hook completed")


def apply_plans(
    plans: list[EnvironmentPlan], cfg: NodeConfig, config_path: Path, *, dry_run: bool
) -> list[str]:
    """Apply plans, returning operator-facing event lines.

    Existing mappings are never changed. A missing mapping receives a prefix
    only after a successful environment create/discovery.
    """
    events: list[str] = []
    if not plans:
        return ["No conda-runtime installed packs require setup."]
    conda = find_conda()
    if not conda and not dry_run:
        raise SetupError(
            "conda was not found; install conda or mamba first, then rerun `hololab pack setup`"
        )
    new_envs: dict[str, str] = {}
    for plan in plans:
        selected_python = select_python_version(plan.python)
        prefix = plan.prefix
        reused_existing = prefix is not None
        if prefix is not None and not prefix.exists():
            raise SetupError(f"configured env {plan.name!r} points to missing path: {prefix}")
        if prefix is None:
            events.append(
                f"{plan.name}: create or reuse conda environment named {plan.name} "
                f"with Python {selected_python}"
            )
            if dry_run:
                for package in plan.packages:
                    events.append(
                        f"{plan.name}: install {package} (conda-forge, then pip fallback)"
                    )
                continue
            # conda create -n is itself safe with a pre-existing name only if
            # omitted; first attempt to discover it without modifying anything.
            try:
                prefix = _prefix_for_name(conda, plan.name)
                reused_existing = True
                events.append(
                    f"{plan.name}: found existing environment {prefix} (will not rebuild)"
                )
            except SetupError:
                command = [conda, "create", "--yes", "--name", plan.name]
                # An empty environment has no interpreter; `conda run python`
                # can then fall through to a parent PATH and report a wrong
                # prefix. Always install an explicitly selected Python minor;
                # unconstrained conda resolution otherwise selects the newest
                # interpreter, often ahead of package wheel support.
                command.append(f"python={selected_python}")
                result = _run(command)
                if result.returncode:
                    raise SetupError(
                        f"failed to create {plan.name!r}: {result.stderr.strip()}"
                    ) from None
                prefix = _prefix_for_name(conda, plan.name)
            new_envs[plan.name] = str(prefix)
        if reused_existing:
            if plan.prefix is not None:
                # A configured mapping is always preserved, even if its Python
                # is too new for the package ecosystem.
                events.append(f"{plan.name}: use existing mapping {prefix} (will not rebuild)")
            installed_python = _environment_python_version(conda, prefix) if not dry_run else None
            if installed_python is None:
                events.append(f"{plan.name}: could not determine existing Python version")
            else:
                minor = f"{installed_python.major}.{installed_python.minor}"
                events.append(f"{plan.name}: existing Python {installed_python}")
                if minor not in _ECOSYSTEM_PYTHONS:
                    events.append(
                        f"{plan.name}: warning: Python {installed_python} is outside the "
                        "ecosystem-compatible range (3.10-3.13); setup will not rebuild it"
                    )
        for package in plan.packages:
            if dry_run:
                events.append(
                    f"{plan.name}: check/install {package} (conda-forge, then pip fallback)"
                )
                continue
            if _satisfied(conda, prefix, package):
                events.append(f"{plan.name}: {package} already satisfied")
                continue
            events.append(f"{plan.name}: install {package} (conda-forge, then pip fallback)")
            if dry_run:
                continue
            result = _run(
                [conda, "install", "--yes", "--prefix", str(prefix), "-c", "conda-forge", package]
            )
            if result.returncode:
                pip = _run(
                    [
                        conda,
                        "run",
                        "--prefix",
                        str(prefix),
                        "python",
                        "-m",
                        "pip",
                        "install",
                        package,
                    ]
                )
                if pip.returncode:
                    raise SetupError(
                        f"failed to install {package!r} in {plan.name!r}; conda: {result.stderr.strip()}; pip: {pip.stderr.strip()}"
                    )
        _apply_hooks(plan, conda, prefix, events, dry_run)
    if new_envs and not dry_run:
        if config_path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = config_path.with_name(config_path.name + f".setup-{stamp}.bak")
            shutil.copy2(config_path, backup)
            events.append(f"backed up config to {backup}")
        raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        raw = raw or {}
        envs = raw.setdefault("envs", {})
        for name, prefix in new_envs.items():
            envs.setdefault(name, prefix)
            events.append(f"{name}: added mapping {envs[name]}")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return events
