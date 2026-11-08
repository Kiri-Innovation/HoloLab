"""Pinned upstream weights shared by setup and inference; never stored in the pack."""

import hashlib
import os
from pathlib import Path

REPO = "ZhengPeng7/BiRefNet"
REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"
SHA256 = "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154"


def weight_path():
    home = Path(os.environ.get("HOLOLAB_HOME", Path.home() / ".hololab"))
    hf_home = Path(os.environ.get("HF_HOME", home / "node/weights/huggingface"))
    return (
        Path(os.environ.get("BIREFNET_WEIGHTS_DIR", hf_home.parent / "birefnet"))
        / "model.safetensors"
    )


def verify(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SHA256:
        raise RuntimeError(
            f"BiRefNet checksum mismatch at {path}: expected {SHA256}, got {digest.hexdigest()}. Remove the corrupt cache file and rerun pack setup."
        )


def main():
    import shutil

    from huggingface_hub import hf_hub_download

    target = weight_path()
    if target.is_file():
        verify(target)
        print(f"BIREFNET_WEIGHTS_CACHED {target} sha256={SHA256}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = hf_hub_download(repo_id=REPO, revision=REVISION, filename="model.safetensors")
    except Exception as exc:
        raise RuntimeError(
            f"BiRefNet download failed: {REPO}@{REVISION}. Check HF_ENDPOINT, HTTPS_PROXY and Hugging Face connectivity; rerun pack setup after restoring access."
        ) from exc
    import tempfile

    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        verify(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"BIREFNET_WEIGHTS_DOWNLOADED {target} sha256={SHA256}")


if __name__ == "__main__":
    main()
