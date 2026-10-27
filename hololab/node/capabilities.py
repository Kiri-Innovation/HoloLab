"""Metadata-only environment checks; never installs or imports algorithm packages."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from hololab.node import resource_lens

BOUNDARY = "本地能力检查只覆盖已声明且在可探测范围内的项; 通过不代表一定能运行。环境未冻结、未被代码身份覆盖。"
# Hardware discovery invokes ``nvidia-smi`` and is markedly slower than the
# metadata fingerprint.  GPU topology cannot change during a normal process
# lifetime, while a visibility mask can, so cache it briefly per exact mask.
# A user-requested fresh check always bypasses this cache.
GPU_PROBE_CACHE_TTL_S = 30.0
PROBE = """import sys,json,os,importlib.metadata as m
items = list(m.distributions())
packages = {}
for d in items:
 if d.metadata["Name"]: packages.setdefault(d.metadata["Name"].lower().replace("_","-"),d.version)
print(json.dumps({"python":sys.version.split()[0],
 "packages":packages, "cuda_visible_devices":os.environ.get("CUDA_VISIBLE_DEVICES"),
 "paths":list(set(sys.path+[str(d.locate_file("")) for d in items]))}))
"""


def stamp(path: Path):
    try:
        s = path.stat()
        return str(path), s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size
    except FileNotFoundError:
        return str(path), None


def fingerprint(prefix: Path, paths: list[str]) -> str:
    """Stat installation metadata, not package code. Detect pip/conda/editable installs."""
    roots = {prefix / "conda-meta", *prefix.glob("lib/python*/site-packages")}
    roots.update(Path(p) for p in paths if p and Path(p).is_absolute())
    stamps = [stamp(prefix), stamp(prefix / "bin/python")]
    for root in sorted(roots):
        stamps.append(stamp(root))
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if (
                p.name.endswith((".dist-info", ".egg-info", ".egg-link", ".pth"))
                or root.name == "conda-meta"
            ):
                stamps.append(stamp(p))
                if p.is_dir():
                    stamps.extend(
                        stamp(p / name) for name in ("METADATA", "PKG-INFO", "direct_url.json")
                    )
    return hashlib.sha256(json.dumps(stamps).encode()).hexdigest()


def probe_environment(prefix: Path, conda_bin: str, activated: dict | None) -> dict:
    if not prefix.is_dir() or not (prefix / "bin/python").is_file():
        raise ValueError(f"环境目录或 Python 不存在: {prefix}")
    if activated is not None:
        command = [str(prefix / "bin/python"), "-c", PROBE]
        env = activated
    else:
        from hololab.node.env_cache import _daemon_env

        command = [
            conda_bin,
            "run",
            "-p",
            str(prefix),
            "--no-capture-output",
            "python",
            "-c",
            PROBE,
        ]
        env = _daemon_env()
    result = subprocess.run(
        command, env=env, cwd=prefix, capture_output=True, text=True, timeout=20, check=True
    )
    data = json.loads(result.stdout)
    data["packages"] = {canonicalize_name(k): v for k, v in data["packages"].items()}
    data["package_sources"] = dict.fromkeys(data["packages"], "python-distribution")
    for metadata in sorted((prefix / "conda-meta").glob("*.json")):
        item = json.loads(metadata.read_text())
        name = canonicalize_name(item["name"])
        if name not in data["packages"]:
            data["packages"][name] = item["version"]
            data["package_sources"][name] = "conda-meta"
    return data


def probe_gpus(visible: str | None) -> dict:
    gpus = []
    for row in resource_lens.query("index,uuid,name,memory.total,driver_version", "gpu"):
        index, uuid, name, memory, driver = (v.strip() for v in row)
        if (
            visible is not None
            and index not in visible.split(",")
            and uuid not in visible.split(",")
        ):
            continue
        gpus.append(
            {
                "index": index,
                "uuid": uuid,
                "name": name,
                "vram_gb": float(memory) / 1024,
                "driver": driver,
                "arch": None,
            }
        )
    try:
        arches = {
            r[0].strip(): "sm" + r[1].strip().replace(".", "")
            for r in resource_lens.query("uuid,compute_cap", "gpu")
        }
        for gpu in gpus:
            gpu["arch"] = arches.get(gpu["uuid"])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass  # Unknown architecture only fails an explicit arch requirement.
    cuda = None
    try:
        result = subprocess.run(
            ["nvidia-smi", "-q", "-x"], capture_output=True, text=True, timeout=3, check=True
        )
        cuda = ET.fromstring(result.stdout).findtext("cuda_version")
    except (OSError, subprocess.SubprocessError, ET.ParseError):
        pass
    return {"gpus": gpus, "driver_cuda": cuda}


def enforce(report: dict) -> None:
    if report["status"] in ("missing", "unknown"):
        details = "; ".join(
            f"{item['item']}: 要求={item['required']}; 当前={item['actual']}; {item['remedy']}"
            for item in report["issues"]
        )
        raise ValueError("本地能力检查失败, 任务未启动。" + details)


class CapabilityChecker:
    def __init__(self):
        self._cache: dict = {}
        self._gpu_cache: dict[str | None, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def check(
        self, manifest, envs: dict, conda_bin: str, activated: dict | None = None, *, fresh=False
    ) -> dict:
        contract = manifest.requires
        required = contract.as_dict() if contract is not None else None
        report = {
            "status": "undeclared" if contract is None else "satisfied",
            "checked_at": None,
            "issues": [],
            "boundary": BOUNDARY,
            "environment_covered": False,
            "runtime_verified": False,
            "env": manifest.runtime.env,
            "requires": required,
        }
        if contract is None:
            return report  # Historical packages must not acquire a new failure mode.
        if contract.is_empty():
            # ``requires: {}`` is an affirmative compatibility statement, not a
            # request to validate the runtime's default environment mapping.
            return report
        report["checked_at"] = time.time()

        def issue(item, required, actual, remedy, *, unknown=False):
            report["status"] = "unknown" if unknown else "missing"
            report["issues"].append(
                dict(item=item, required=required, actual=actual, remedy=remedy)
            )

        prefix_value = envs.get(manifest.runtime.env)
        if not prefix_value:
            issue(
                "env", manifest.runtime.env, "未映射", "在节点 config.yaml 的 envs 配置已有环境路径"
            )
            return report
        prefix = Path(prefix_value)
        report["prefix"] = str(prefix)
        visible = (activated or {}).get("CUDA_VISIBLE_DEVICES")
        try:
            with self._lock:
                key = (str(prefix), conda_bin, json.dumps(activated, sort_keys=True))
                cached = self._cache.get(key)
                mark = fingerprint(prefix, cached[1]["paths"] if cached else [])
                if not fresh and cached and cached[0] == mark:
                    data = copy.deepcopy(cached[1])
                    report["cache_hit"] = True
                else:
                    data = probe_environment(prefix, conda_bin, activated)
                    after = fingerprint(prefix, data["paths"])
                    # Probe again if an install crossed the first read. Never cache a
                    # mixed inventory under a newer installation fingerprint.
                    if mark != after:
                        data = probe_environment(prefix, conda_bin, activated)
                        if fingerprint(prefix, data["paths"]) != after:
                            raise ValueError("环境元数据在检查期间持续变化, 请安装结束后重新检查")
                    if len(self._cache) >= 64:
                        self._cache.clear()
                    self._cache[key] = (after, copy.deepcopy(data))
                    report["cache_hit"] = False
                report["environment_fingerprint"] = self._cache[key][0]
            report["python"] = data["python"]
            visible = data.get("cuda_visible_devices", visible)
            if contract.python:
                spec = contract.python
                spec = spec if spec.startswith(("=", "<", ">", "!", "~")) else "==" + spec
                if Version(data["python"]) not in SpecifierSet(spec):
                    issue(
                        "python",
                        contract.python,
                        data["python"],
                        "选择满足版本要求的已有 Python 环境",
                    )
            report["packages"] = {}
            report["package_sources"] = {}
            for text in contract.packages:
                req = Requirement(text)
                installed = data["packages"].get(canonicalize_name(req.name))
                report["packages"][req.name] = installed
                report["package_sources"][req.name] = data.get("package_sources", {}).get(
                    canonicalize_name(req.name)
                )
                try:
                    matches = installed is not None and (
                        not req.specifier or Version(installed) in req.specifier
                    )
                except InvalidVersion:
                    matches = False
                if not matches:
                    issue(
                        "package:" + req.name,
                        text,
                        installed or "未安装",
                        "在所选环境手动安装/调整该发行包, 再重新检查",
                    )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            issue(
                "env",
                str(prefix),
                str(exc),
                "检查环境路径、Python 和 conda 配置后重试",
                unknown=True,
            )
        if contract.gpu is not None or contract.cuda:
            try:
                with self._lock:
                    gpu_cached = self._gpu_cache.get(visible)
                    now = time.monotonic()
                    if (
                        not fresh
                        and gpu_cached is not None
                        and now - gpu_cached[0] < GPU_PROBE_CACHE_TTL_S
                    ):
                        hardware = copy.deepcopy(gpu_cached[1])
                        report["hardware_cache_hit"] = True
                    else:
                        # Keep discovery inside the lock: a workflow can issue
                        # many capability requests at once, and they should
                        # share one expensive nvidia-smi invocation.
                        hardware = probe_gpus(visible)
                        if len(self._gpu_cache) >= 16:
                            self._gpu_cache.clear()
                        self._gpu_cache[visible] = (time.monotonic(), copy.deepcopy(hardware))
                        report["hardware_cache_hit"] = False
                report["hardware"] = hardware
                if contract.gpu is not None:
                    gpu_req = contract.gpu
                    matches = [
                        g
                        for g in hardware["gpus"]
                        if (gpu_req.vram_gb is None or g["vram_gb"] >= gpu_req.vram_gb)
                        and (not gpu_req.arch or g["arch"] in gpu_req.arch)
                    ]
                    if not matches:
                        issue(
                            "gpu",
                            gpu_req.model_dump(),
                            hardware["gpus"],
                            "选择有满足显存和架构要求的可见 GPU 的节点",
                        )
                if contract.cuda and (
                    not hardware["driver_cuda"]
                    or Version(hardware["driver_cuda"]) < Version(contract.cuda)
                ):
                    issue(
                        "cuda_driver",
                        contract.cuda,
                        hardware["driver_cuda"] or "无法探测",
                        "选择支持所需 CUDA 版本的驱动/节点; 此项不检查 toolkit",
                    )
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                issue(
                    "gpu",
                    "可探测 GPU/驱动",
                    str(exc),
                    "检查 NVIDIA 驱动及 nvidia-smi 可用性",
                    unknown=True,
                )
        return report
