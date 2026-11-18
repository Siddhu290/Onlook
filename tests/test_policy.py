"""Scenario tests with a fake clock: 10 fps ticks, t = i / 10 keeps times exact."""
from dataclasses import dataclass

from shoulder_guard.policy import AWAY_LOCK, LOCK, WARN, Policy

FPS = 10


@dataclass
class T:  # stand-in for a smoothed track
    is_owner: bool = False
    looking: bool = False


ME = T(is_owner=True, looking=True)
WATCHER = T(looking=True)
TURNED_AWAY = T(looking=False)


def make(**kw):
    args = dict(warn_after=1.0, lock_after=5.0, grace=0.8, cooldown=15.0, away_lock=0.0)
    args.update(kw)
    return Policy(**args)


def simulate(policy, scene, seconds, enrolled=True):
    """scene(t) -> tracks. Returns [(t, kind)]."""
    events = []
    for i in range(int(seconds * FPS) + 1):
        t = i / FPS
        events += [(t, a.kind) for a in policy.step(t, scene(t), enrolled)]
    return events


def test_stranger_watches_5s():
    assert simulate(make(), lambda t: [ME, WATCHER], 6) == [(1.0, WARN), (5.0, LOCK)]


def test_short_glance_ignored():
    assert simulate(make(), lambda t: [ME, WATCHER] if t < 0.6 else [ME], 5) == []


def test_glance_then_grace_never_counts_as_watching():
    # seen 0..0.9s (0.9s watched), then gone. The grace window (0.8s) must not
    # push watched time over warn_after=1.0.
    p = make()
    assert simulate(p, lambda t: [ME, WATCHER] if t < 0.95 else [ME], 5) == []
    assert p.watched == 0.0


def test_head_turned_away_ignored():
    assert simulate(make(), lambda t: [ME, TURNED_AWAY], 10) == []


def test_flicker_keeps_episode():
    def scene(t):
        return [ME] if 2.0 <= t < 2.3 else [ME, WATCHER]
    assert simulate(make(), scene, 6) == [(1.0, WARN), (5.0, LOCK)]


def test_owner_alone():
    assert simulate(make(away_lock=2), lambda t: [ME], 20) == []


def test_owner_leaves_away_lock_once():
    events = simulate(make(away_lock=2, cooldown=1), lambda t: [ME] if t < 1 else [], 30)
    assert events == [(2.9, AWAY_LOCK)]  # last seen at 0.9s


def test_away_rearms_when_owner_returns():
    events = simulate(make(away_lock=2, cooldown=0),
                      lambda t: [] if t < 3 or t >= 4 else [ME], 10)
    assert events == [(2.0, AWAY_LOCK), (5.9, AWAY_LOCK)]


def test_away_needs_enrollment():
    assert simulate(make(away_lock=2), lambda t: [], 10, enrolled=False) == []


def test_cooldown_suppresses_retrigger():
    events = simulate(make(cooldown=15), lambda t: [ME, WATCHER], 30)
    # lock at 5, cooldown till 20, new episode starts at 20 -> warn 21, lock 25
    assert events == [(1.0, WARN), (5.0, LOCK), (21.0, WARN), (25.0, LOCK)]


def test_episode_starting_at_time_zero():
    p = make()
    p.step(0.0, [WATCHER], True)
    assert p.episode_start == 0.0  # the prototype's `or now` lost a 0.0 start
