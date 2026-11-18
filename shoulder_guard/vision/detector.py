"""YuNet face detector."""
from __future__ import annotations

import cv2
import numpy as np

INITIAL_INPUT_SIZE = (320, 320)  # replaced by the real frame size on the first detect()


class Detector:
    def __init__(self, model: str, score: float, nms: float, top_k: int, min_face_px: int):
        self.net = cv2.FaceDetectorYN.create(str(model), "", INITIAL_INPUT_SIZE, score, nms, top_k)
        self.min_face_px = min_face_px
        self._size = None

    def detect(self, frame) -> list[np.ndarray]:
        """Face rows [x, y, w, h, 10 landmark coords, score], too-small faces dropped."""
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self.net.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self.net.detect(frame)
        if faces is None:
            return []
        return [f for f in faces if min(f[2], f[3]) >= self.min_face_px]
