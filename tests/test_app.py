from types import SimpleNamespace

import numpy as np

from shoulder_guard import app, config
from shoulder_guard.policy import LOCK, Action
from shoulder_guard.vision.tracker import Tracker


def face(x, size, nose_dx=0):
    """YuNet-style row; a frontal face unless nose_dx turns it."""
    c = x + size / 2
    pts = [c - 15, 50, c + 15, 50, c + nose_dx, 65, c - 12, 80, c + 12, 80]
    return np.array([x, 30, size, size, *pts, 0.9], dtype=float)


def cfg_for(tmp_path, *overrides):
    return config.load(tmp_path / "c.toml", [f"paths.home='{tmp_path}'", *overrides])


def test_fallback_biggest_face_is_owner(tmp_path):
    cfg = cfg_for(tmp_path)
    tr = Tracker(1.0, 0.25, 7, 5)
    faces = [face(0, 60), face(200, 120)]
    tracks = app.analyse(None, 0.0, faces, None, tr, None, cfg)
    assert [t.is_owner for t in tracks] == [False, True]
    assert all(t.looking for t in tracks)


def test_turned_head_not_looking_and_gallery_used(tmp_path):
    cfg = cfg_for(tmp_path)
    tr = Tracker(1.0, 0.25, 7, 5)
    g = np.eye(128, dtype=np.float32)[:1]
    embeds = {0: g[0], 200: -g[0]}
    tracks = app.analyse(None, 0.0, [face(0, 60), face(200, 60, nose_dx=14)],
                         lambda frame, f: embeds[int(f[0])], tr, g, cfg)
    assert [t.is_owner for t in tracks] == [True, False]
    assert [t.looking for t in tracks] == [True, False]


def test_collect_samples_rules():
    clock = SimpleNamespace(t=0.0)

    def tick():
        clock.t += 0.25  # one frame; binary-exact
        return clock.t
    ident = SimpleNamespace(enroll_interval=0.5, enroll_samples=3, enroll_timeout=90)
    # 2 faces / 0 faces are never captured; captures are >= 0.5s (2 frames) apart
    counts = iter([2, 0, 1, 1, 1, 1, 1, 1, 1, 1])
    feats = app.collect_samples(range(10), lambda f: ["f"] * next(counts),
                                lambda fr, f: fr, ident, clock=tick)
    assert feats == [2, 4, 6]


def test_collect_samples_timeout():
    clock = SimpleNamespace(t=0.0)

    def tick():
        clock.t += 1.0
        return clock.t
    ident = SimpleNamespace(enroll_interval=0, enroll_samples=99, enroll_timeout=3)
    feats = app.collect_samples(range(100), lambda f: ["f"], lambda fr, f: fr, ident, clock=tick)
    assert len(feats) == 3  # start read at t=1; frames at t=2,3,4; stops once 4-1 >= 3


def test_collect_samples_abort():
    ident = SimpleNamespace(enroll_interval=0, enroll_samples=99, enroll_timeout=90)
    feats = app.collect_samples(range(10), lambda f: ["f"], lambda fr, f: fr, ident,
                                show=lambda *a: False)
    assert feats == [0]


def test_perform_dry_run_lock_without_snapshot(tmp_path, monkeypatch):
    cfg = cfg_for(tmp_path, "actions.dry_run=true", "actions.notify=false")
    called = []
    monkeypatch.setattr(app.lock, "lock_screen", lambda dry, timeout: called.append(dry))
    app.perform(cfg, Action(LOCK, "test"), np.zeros((4, 4, 3), np.uint8))
    assert called == [True]
    assert not (tmp_path / "snapshots").exists()


def test_perform_snapshot_opt_in(tmp_path, monkeypatch):
    cfg = cfg_for(tmp_path, "actions.dry_run=true", "actions.notify=false",
                  "actions.snapshot_on_lock=true")
    monkeypatch.setattr(app.lock, "lock_screen", lambda dry, timeout: True)
    app.perform(cfg, Action(LOCK, "test"), np.zeros((4, 4, 3), np.uint8))
    assert len(list((tmp_path / "snapshots").glob("*.jpg"))) == 1


def test_runs_without_tray_deps(tmp_path, monkeypatch):
    import sys

    from shoulder_guard import tray
    monkeypatch.setitem(sys.modules, "pystray", None)  # import now raises ImportError
    assert tray.start_tray(app.State(), cfg_for(tmp_path)) is None
