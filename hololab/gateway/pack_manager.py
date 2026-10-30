"""Registry browsing and safe, node-local installation helpers."""

# ruff: noqa: RUF001 -- Chinese user-facing messages use Chinese punctuation.
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from hololab.pack_registry import HashMismatch, RegistryError, SourceUnavailable, read_index
from hololab.package_identity import canonical_name, parse_spec, validate_name


def http_source(source: str) -> str:
    parsed = urlparse(source)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("请输入 HTTP(S) 注册表根地址。")
    return source.rstrip("/") + "/"


def browse(source: str, name: str | None = None):
    source = http_source(source)
    if name:
        return read_index(source, canonical_name(validate_name(name))).model_dump()
    with urlopen(source + "packs/", timeout=15) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise RegistryError("invalid catalog")
    data = json.loads(raw)
    names = [canonical_name(validate_name(p["name"])) for p in data["projects"]]
    return {"projects": [{"name": name} for name in sorted(set(names))]}


def public_error(exc: Exception) -> dict:
    cause = exc.__cause__
    if isinstance(exc, HashMismatch):
        code, message = "hash_mismatch", "代码或下载包哈希不匹配，已停止安装，未覆盖已有包。"
    elif (isinstance(exc, HTTPError) and exc.code == 404) or (
        isinstance(cause, HTTPError) and cause.code == 404
    ):
        code, message = "not_found", "注册表中不存在这个包或版本。"
    elif isinstance(exc, (SourceUnavailable, URLError, TimeoutError, ConnectionError)):
        code, message = "source_unavailable", "源不可达，请检查注册表地址和网络后重试。"
    elif isinstance(exc, OSError):
        code, message = "disk_error", "无法写入包目录，请检查磁盘空间和目录权限。"
    elif "yank" in str(exc):
        code, message = "yanked", "该版本已 yank，禁止新安装。"
    elif "找不到" in str(exc):
        code, message = "not_found", "注册表中不存在这个包或版本。"
    elif "overwrite" in str(exc) or "ambiguous" in str(exc):
        code, message = "conflict", "本地包或目录存在冲突，请处理后重试；不会覆盖已有内容。"
    else:
        code, message = "invalid_package", "注册表或包内容无效，请检查来源或联系发布者。"
    return {"code": code, "message": message}


def installation_destination(pack_dirs: list[Path], install_dir: Path | None = None) -> Path | None:
    from hololab.pack_install import RECEIPT

    if install_dir is not None:
        return install_dir
    # Match the scanner's precise-file, direct-pack and repository-container modes.
    destination = None
    for root in pack_dirs:
        candidate = root
        seen = set()
        while True:
            if candidate.resolve() in seen or candidate.is_file():
                break
            seen.add(candidate.resolve())
            if (candidate / "manifest.yaml").exists() or (candidate / RECEIPT).exists():
                break
            if not candidate.exists() and candidate.suffix in {".yaml", ".yml"}:
                break
            if (candidate / "packs").is_dir():
                candidate = candidate / "packs"
                continue
            destination = candidate
            break
        if destination is not None:
            break
    return destination


def install_on_node(
    spec: str,
    source: str,
    pack_dirs: list[Path],
    expected: str | None,
    install_dir: Path | None = None,
):
    from hololab.pack_install import install

    source = http_source(source)
    parse_spec(spec)
    destination = installation_destination(pack_dirs, install_dir)
    if destination is None:
        return {
            "error": {
                "code": "no_destination",
                "message": "没有可安装的包集合目录，请先在计算节点 pack_dirs 配置一个目录。",
            }
        }
    try:
        # UI installation always checks current publication, even if already local.
        from hololab.pack_registry import find_release

        _, release = find_release(spec, [source], expected)
        if release.yanked:
            raise RegistryError("yanked")
        result = install(spec, [source], destination, pack_dirs, expected or release.code_sha256)
        return {**result, "destination": str(destination)}
    except Exception as exc:
        return {"error": public_error(exc)}
