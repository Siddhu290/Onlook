"""Head yaw/pitch from YuNet's 5 landmarks, measured in the eye-line's own frame
so head roll doesn't affect it.
  yaw   = 0 facing the camera, ~0.6 at ~30 deg, grows monotonically with the turn
  pitch ~ 0.5 level; smaller = looking up, bigger = looking down
"""
from __future__ import annotations

import numpy as np

EPS = 1e-9


def head_pose(face) -> tuple[float, float]:
    right_eye, left_eye, nose, mouth_r, mouth_l = (
        np.asarray(face[4:14], dtype=np.float64).reshape(5, 2))
    eye_mid, mouth_mid = (right_eye + left_eye) / 2, (mouth_r + mouth_l) / 2
    v = left_eye - right_eye
    dist = np.linalg.norm(v) + EPS
    u = v / dist
    perp = np.array([-u[1], u[0]])
    n = nose - eye_mid
    yaw = float(np.dot(n, u) / (dist / 2))
    pitch = float(np.dot(n, perp) / (np.dot(mouth_mid - eye_mid, perp) + EPS))
    return yaw, pitch
