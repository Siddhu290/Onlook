"""Webcam that survives being busy (Teams/Meet) or unplugged."""
from __future__ import annotations

import logging
import sys
import time

import cv2

log = logging.getLogger("shoulder_guard")


def open_capture(index: int, width: int, height: int):
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


class Camera:
    """read() never blocks on reconnects: it returns None while the camera is down
    and retries opening every `retry_seconds`."""

    def __init__(self, cfg, on_unavailable=lambda: None, opener=open_capture,
                 clock=time.monotonic):
        self.cfg, self.on_unavailable, self.opener, self.clock = cfg, on_unavailable, opener, clock
        self.cap = None
        self.fails = 0
        self.lost = False
        self.next_open = 0.0

    @property
    def available(self) -> bool:
        return not self.lost

    def read(self):
        if self.cap is None:
            if self.clock() < self.next_open:
                return None
            self.cap = self.opener(self.cfg.index, self.cfg.width, self.cfg.height)
        ok, frame = self.cap.read() if self.cap.isOpened() else (False, None)
        if ok:
            if self.lost:
                log.info("Camera is back.")
            self.fails, self.lost = 0, False
            return frame
        self.fails += 1
        if self.fails >= self.cfg.reconnect_after_failures or self.lost:
            if not self.lost:
                self.lost = True
                log.warning("Camera unavailable after %d failed reads; retrying every %.0fs.",
                            self.fails, self.cfg.retry_seconds)
                self.on_unavailable()
            self.release()
            self.next_open = self.clock() + self.cfg.retry_seconds
        return None

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
