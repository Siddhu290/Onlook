"""Download the ONNX models from opencv_zoo and verify them against pinned SHA-256s."""
from __future__ import annotations

import hashlib
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger("shoulder_guard")

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
# Expected hashes = the `oid sha256:` lines of the Git-LFS pointer files at
# raw.githubusercontent.com/opencv/opencv_zoo/main/models/<path>.
MODELS = {
    "yunet": ("face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": ("face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
LFS_POINTER_MAX_BYTES = 1024  # a real model is far bigger; a pointer is ~130 bytes
CHUNK = 1 << 20
DOWNLOAD_TIMEOUT = 60


class ModelError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def model_file(key: str, models_dir: Path) -> Path:
    return Path(models_dir) / Path(MODELS[key][0]).name


def status(key: str, models_dir: Path) -> str:
    """'ok', 'missing' or 'bad hash' (no download)."""
    path = model_file(key, models_dir)
    if not path.exists():
        return "missing"
    return "ok" if sha256(path) == MODELS[key][1] else "bad hash"


def ensure(key: str, models_dir: Path, fetch=urllib.request.urlopen) -> Path:
    """Return the verified local model path, downloading it first if needed."""
    rel, expected = MODELS[key]
    url, path = ZOO + rel, model_file(key, models_dir)
    manual = (f"Download it manually in a browser from\n  {url}\n"
              f"and save it as\n  {path}")
    if path.exists() and sha256(path) == expected:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    log.info("Downloading %s ...", path.name)
    try:
        with fetch(url, timeout=DOWNLOAD_TIMEOUT) as resp, open(part, "wb") as out:
            while block := resp.read(CHUNK):
                out.write(block)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise ModelError(f"Download of {path.name} failed ({e}).\n{manual}") from e
    if part.stat().st_size <= LFS_POINTER_MAX_BYTES:
        part.unlink()
        raise ModelError(f"Got a Git-LFS pointer instead of {path.name}.\n{manual}")
    got = sha256(part)
    if got != expected:
        part.unlink()
        raise ModelError(f"{path.name}: SHA-256 mismatch (got {got}, want {expected}).\n{manual}")
    part.replace(path)
    log.info("Verified %s", path.name)
    return path
