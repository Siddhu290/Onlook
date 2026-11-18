import subprocess
from types import SimpleNamespace

import pytest

from shoulder_guard.platform import lock, notify
from shoulder_guard.platform.camera import Camera

# ---------------------------------------------------------------- camera


class FakeCap:
    def __init__(self, script):
        self.script = script  # shared list of bools: next read results

    def isOpened(self):
        return True

    def read(self):
        ok = self.script.pop(0) if self.script else False
        return ok, ("frame" if ok else None)

    def release(self):
        pass


def make_camera(script):
    clock = SimpleNamespace(t=0.0)
    opened = []
    cfg = SimpleNamespace(index=0, width=640, height=480,
                          reconnect_after_failures=3, retry_seconds=2.0)
    alerts = []

    def opener(*a):
        opened.append(clock.t)
        return FakeCap(script)
    cam = Camera(cfg, on_unavailable=lambda: alerts.append(clock.t), opener=opener,
                 clock=lambda: clock.t)
    return cam, clock, opened, alerts


def test_camera_reconnect_cycle():
    script = [True, False, False, False, False, True]
    cam, clock, opened, alerts = make_camera(script)
    assert cam.read() == "frame"
    assert [cam.read() for _ in range(3)] == [None] * 3
    assert not cam.available and alerts == [0.0]
    assert cam.read() is None and len(opened) == 1  # still waiting for retry
    clock.t = 2.0
    assert cam.read() is None and len(opened) == 2  # retry failed
    clock.t = 4.0
    assert cam.read() == "frame" and cam.available
    assert alerts == [0.0]  # notified once


# ------------------------------------------------------------------ lock

def fake_run(outputs):
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        rc, out = outputs.get(cmd[0] + (" " + cmd[1] if len(cmd) > 1 else ""), (1, ""))
        return subprocess.CompletedProcess(cmd, rc, out, "err")
    return run, calls


LIST = ("      2 1001 bob  seat0 tty2\n"
        "     11 {uid} me   -     pts/1\n"
        "      4 {uid} me   seat0 tty2\n")


def test_session_id_lookup():
    import os
    run, _ = fake_run({"loginctl list-sessions": (0, LIST.format(uid=os.getuid()))})
    assert lock.session_id(5, env={}, run=run) == "4"
    assert lock.session_id(5, env={"XDG_SESSION_ID": "9"}, run=run) == "9"


def test_linux_lock_order_and_fallthrough(monkeypatch):
    import os
    monkeypatch.setattr(lock.sys, "platform", "linux")
    monkeypatch.delenv("XDG_SESSION_ID", raising=False)
    run, calls = fake_run({"loginctl list-sessions": (0, LIST.format(uid=os.getuid())),
                           "xdg-screensaver lock": (0, "")})
    assert lock.lock_screen(dry_run=False, timeout=5, run=run) is True
    assert calls[1:] == [lock.GNOME_DBUS, ["loginctl", "lock-session", "4"],
                         ["xdg-screensaver", "lock"]]


def test_nothing_works(monkeypatch, caplog):
    monkeypatch.setattr(lock.sys, "platform", "linux")

    def run(cmd, timeout):
        raise FileNotFoundError(cmd[0])
    assert lock.lock_screen(dry_run=False, timeout=5, run=run) is False
    assert "Could not lock the screen" in caplog.text


def test_dry_run_never_runs_anything(caplog):
    def run(cmd, timeout):
        raise AssertionError("must not run")
    assert lock.lock_screen(dry_run=True, timeout=5, run=run) is True
    assert "would lock" in caplog.text


# ---------------------------------------------------------------- notify

def test_osascript_message_is_argv_not_code():
    cmd = notify.command('He said "hi"', "it's", platform="darwin")
    assert cmd[-2:] == ['He said "hi"', "it's"]
    assert all('"hi"' not in part for part in cmd[:-2])


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_notify_never_raises(monkeypatch, platform):
    monkeypatch.setattr(notify.sys, "platform", platform)

    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(notify.subprocess, "Popen", boom)
    notify.notify("t", "m")
    notify.beep()
