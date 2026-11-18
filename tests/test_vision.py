import math

import numpy as np
import pytest

from shoulder_guard.vision.pose import head_pose
from shoulder_guard.vision.recognizer import load_gallery, save_gallery, similarity
from shoulder_guard.vision.tracker import Tracker, biggest_index, majority

# Image coords (y down). YuNet order: right eye, left eye, nose, right mouth, left mouth.
FRONTAL = np.array([[-30, 0], [30, 0], [0, 30], [-25, 60], [25, 60]], dtype=float)


def face_row(pts, center=(200, 200)):
    pts = np.asarray(pts, dtype=float) + center
    return np.concatenate([[0, 0, 100, 100], pts.ravel(), [0.9]])


def rotate(pts, deg):
    a = math.radians(deg)
    r = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return pts @ r.T


def test_frontal_pose():
    yaw, pitch = head_pose(face_row(FRONTAL))
    assert yaw == pytest.approx(0, abs=1e-6)
    assert pitch == pytest.approx(0.5)


def test_yaw_monotonic_with_nose_shift():
    yaws = []
    for dx in range(0, 30, 3):
        pts = FRONTAL.copy()
        pts[2, 0] += dx
        yaws.append(head_pose(face_row(pts))[0])
    assert all(b > a for a, b in zip(yaws, yaws[1:]))
    assert yaws[6] == pytest.approx(0.6)  # nose 18px over a 30px half eye distance


@pytest.mark.parametrize("deg", [25, -25])
def test_roll_invariant(deg):
    pts = FRONTAL.copy()
    pts[2, 0] += 12
    base = head_pose(face_row(pts))
    rolled = head_pose(face_row(rotate(pts, deg)))
    assert rolled == pytest.approx(base, abs=1e-9)


def make_tracker(ttl=1.0):
    return Tracker(ttl=ttl, iou_match=0.25, owner_window=7, look_window=5)


def test_ids_stable_and_aligned():
    tr = make_tracker()
    a, b = (0, 0, 50, 50), (200, 0, 50, 50)
    t1 = tr.update([a, b], 0.0)
    t2 = tr.update([(205, 2, 50, 50), (3, 1, 50, 50)], 0.1)  # order swapped, moved a bit
    assert [t.id for t in t2] == [t1[1].id, t1[0].id]
    assert len(tr.tracks) == 2


def test_best_iou_wins():
    tr = make_tracker()
    (t,) = tr.update([(0, 0, 100, 100)], 0.0)
    out = tr.update([(40, 0, 100, 100), (2, 0, 100, 100)], 0.1)
    assert out[1].id == t.id and out[0].id != t.id


def test_expiry():
    tr = make_tracker(ttl=1.0)
    (t,) = tr.update([(0, 0, 50, 50)], 0.0)
    tr.update([], 0.9)
    assert tr.tracks == [t]
    tr.update([], 1.1)
    assert tr.tracks == []
    (new,) = tr.update([(0, 0, 50, 50)], 1.2)
    assert new.id != t.id


def test_majority_votes():
    assert majority([]) is False
    assert majority([True, False]) is False  # tie
    assert majority([True, True, False]) is True
    tr = make_tracker()
    (t,) = tr.update([(0, 0, 50, 50)], 0.0)
    for v in [True, True, False, False, True, False]:  # window 5 keeps last 5: T F F T F
        t.look_votes.append(v)
    assert t.looking is False
    t.owner_votes.extend([True, False])
    assert t.is_owner is False


def test_biggest_index():
    assert biggest_index([]) == -1
    assert biggest_index([(0, 0, 10, 10), (0, 0, 30, 30), (0, 0, 20, 20)]) == 1


def test_gallery_roundtrip(tmp_path):
    p = tmp_path / "owner.npy"
    rng = np.random.default_rng(0)
    feats = list(rng.normal(size=(12, 128)))
    g = save_gallery(p, feats, append=False)
    assert g.shape == (12, 128)
    assert np.allclose(np.linalg.norm(g, axis=1), 1)
    assert save_gallery(p, feats[:3], append=True).shape == (15, 128)
    assert load_gallery(p).shape == (15, 128)
    assert similarity(g, g[4]) == pytest.approx(1.0, abs=1e-5)


def test_gallery_bad_shape(tmp_path):
    p = tmp_path / "owner.npy"
    np.save(p, np.zeros((3, 64)))
    with pytest.raises(ValueError, match="enroll"):
        load_gallery(p)
    assert load_gallery(tmp_path / "missing.npy") is None
