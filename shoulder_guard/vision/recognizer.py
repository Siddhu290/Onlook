"""SFace embeddings and the owner gallery (<home>/owner.npy, N x 128, L2-normalised)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

EMBED_DIM = 128  # fixed by the SFace model
EPS = 1e-9


def normalize(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + EPS)


class Recognizer:
    def __init__(self, model: str):
        self.net = cv2.FaceRecognizerSF.create(str(model), "")

    def embed(self, frame, face) -> np.ndarray:
        return normalize(self.net.feature(self.net.alignCrop(frame, face)).flatten())


def similarity(gallery: np.ndarray, emb: np.ndarray) -> float:
    """Max cosine similarity of one normalised embedding against the gallery."""
    return float(np.max(gallery @ emb))


def load_gallery(path: Path) -> np.ndarray | None:
    path = Path(path)
    if not path.exists():
        return None
    g = np.load(path)
    if g.ndim != 2 or g.shape[1] != EMBED_DIM or len(g) == 0:
        raise ValueError(f"{path} has shape {g.shape}, expected N x {EMBED_DIM}. "
                         "Delete it and run `shoulder-guard enroll` again.")
    return normalize(g.astype(np.float32))


def save_gallery(path: Path, feats: list[np.ndarray], append: bool) -> np.ndarray:
    new = normalize(np.stack(feats).astype(np.float32))
    old = load_gallery(path) if append else None
    gallery = new if old is None else np.concatenate([old, new])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.save(path, gallery)
    return gallery
