"""Greedy IoU tracker with majority-vote smoothing of 'is owner' and 'is looking'."""
from __future__ import annotations

import itertools
from collections import deque


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def majority(votes) -> bool:
    """Strict majority; ties and empty = False."""
    return sum(votes) * 2 > len(votes)


class Track:
    def __init__(self, tid: int, box, now: float, owner_window: int, look_window: int):
        self.id, self.box, self.last = tid, box, now
        self.owner_votes: deque[bool] = deque(maxlen=owner_window)
        self.look_votes: deque[bool] = deque(maxlen=look_window)
        self.sim = self.yaw = self.pitch = 0.0

    @property
    def is_owner(self) -> bool:
        return majority(self.owner_votes)

    @property
    def looking(self) -> bool:
        return majority(self.look_votes)


class Tracker:
    def __init__(self, ttl: float, iou_match: float, owner_window: int, look_window: int):
        self.ttl, self.iou_match = ttl, iou_match
        self.windows = (owner_window, look_window)
        self.tracks: list[Track] = []
        self._ids = itertools.count()

    def update(self, boxes, now: float) -> list[Track]:
        """Returns tracks aligned 1:1 with `boxes`. Best-IoU pairs are matched first."""
        pairs = sorted(((iou(t.box, b), ti, bi) for ti, t in enumerate(self.tracks)
                        for bi, b in enumerate(boxes)), reverse=True)
        out: list[Track | None] = [None] * len(boxes)
        used = set()
        for score, ti, bi in pairs:
            if score <= self.iou_match:
                break
            if ti in used or out[bi] is not None:
                continue
            used.add(ti)
            t = out[bi] = self.tracks[ti]
            t.box, t.last = boxes[bi], now
        for bi, b in enumerate(boxes):
            if out[bi] is None:
                out[bi] = Track(next(self._ids), b, now, *self.windows)
                self.tracks.append(out[bi])
        self.tracks = [t for t in self.tracks if now - t.last <= self.ttl]
        return out


def biggest_index(boxes) -> int:
    """Index of the largest box, -1 if none (fallback owner when nobody is enrolled)."""
    return max(range(len(boxes)), key=lambda i: boxes[i][2] * boxes[i][3], default=-1)
