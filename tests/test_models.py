import hashlib
import io

import pytest

from shoulder_guard import models


def fake_fetch(payload):
    def fetch(url, timeout):
        return io.BytesIO(payload)
    return fetch


def test_lfs_pointer_rejected(tmp_path):
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
    with pytest.raises(models.ModelError, match="Git-LFS pointer"):
        models.ensure("yunet", tmp_path, fetch=fake_fetch(pointer))
    assert not list(tmp_path.iterdir())


def test_hash_mismatch_rejected(tmp_path):
    with pytest.raises(models.ModelError, match="SHA-256 mismatch"):
        models.ensure("yunet", tmp_path, fetch=fake_fetch(b"x" * 5000))
    assert not list(tmp_path.iterdir())


def test_good_download(tmp_path, monkeypatch):
    data = b"y" * 5000
    rel, _ = models.MODELS["yunet"]
    monkeypatch.setitem(models.MODELS, "yunet", (rel, hashlib.sha256(data).hexdigest()))
    p = models.ensure("yunet", tmp_path, fetch=fake_fetch(data))
    assert p.read_bytes() == data and models.status("yunet", tmp_path) == "ok"
    models.ensure("yunet", tmp_path, fetch=None)  # cached: no fetch needed


def test_network_error(tmp_path):
    def boom(url, timeout):
        raise OSError("no network")
    with pytest.raises(models.ModelError, match="manually"):
        models.ensure("sface", tmp_path, fetch=boom)
