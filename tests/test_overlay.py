import stat

import pytest

from shoulder_guard import app, config, overlay
from shoulder_guard.cli import main
from shoulder_guard.policy import LOCK, WARN, Action, Policy


def test_keyword_hash_roundtrip(tmp_path):
    stored = overlay.hash_keyword(">>>?")
    assert ">>>?" not in stored
    assert overlay.check_keyword(">>>?", stored)
    assert not overlay.check_keyword(">>>", stored)
    assert not overlay.check_keyword(">>>?", "garbage")
    assert overlay.hash_keyword("a") != overlay.hash_keyword("a")  # salted
    assert overlay.keyword_length(stored) == 4

    path = overlay.save_keyword(tmp_path, "secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert overlay.check_keyword("secret", overlay.load_keyword(tmp_path))
    assert overlay.load_keyword(tmp_path / "nope") is None


def test_unlocks_without_enter():
    stored = overlay.hash_keyword(">>>?")
    assert overlay.typed_unlocks(">>>?", stored)
    assert overlay.typed_unlocks("oops typo >>>?", stored)  # earlier mistakes don't matter
    assert not overlay.typed_unlocks(">>>", stored)
    assert not overlay.typed_unlocks(">>>?x", stored)
    old_format = stored.rsplit("$", 1)[0]  # hash saved before lengths were stored
    assert overlay.check_keyword(">>>?", old_format)
    assert not overlay.typed_unlocks(">>>?", old_format)  # needs Enter until re-set


def test_set_keyword_cli(tmp_path, monkeypatch):
    answers = iter(["kitty", "kitty", "ab", "x1y", "x2y"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    args = ["set-keyword", "--config", str(tmp_path / "c.toml"),
            "--set", f"paths.home='{tmp_path}'"]
    assert main(args) == 0
    assert overlay.check_keyword("kitty", overlay.load_keyword(tmp_path))
    assert main(args) == 1  # too short
    assert main(args) == 1  # mismatch
    assert overlay.check_keyword("kitty", overlay.load_keyword(tmp_path))


class FakeChild:
    def __init__(self, kind, payload=None):
        self.kind, self.payload, self.alive, self.returncode = kind, payload, True, None

    def running(self):
        return self.alive

    def close(self):
        self.alive = False

    def exit(self, rc):
        self.alive, self.returncode = False, rc


def cfg_for(tmp_path, *overrides):
    return config.load(tmp_path / "c.toml", [f"paths.home='{tmp_path}'", *overrides])


@pytest.fixture
def no_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(app.lock, "lock_screen", lambda dry, timeout: calls.append(dry) or True)
    return calls


def test_warn_shows_cat_lock_covers_screen(tmp_path, no_lock):
    overlay.save_keyword(tmp_path, "kitty")
    cfg = cfg_for(tmp_path, "cover.cat_on_warn=true", "cover.on_lock='blur'")
    screen = overlay.ScreenOverlay(tmp_path, spawn=FakeChild)
    app.perform(cfg, Action(WARN, "w"), None, screen)
    assert screen.cat.kind == "cat"
    app.perform(cfg, Action(LOCK, "l"), None, screen)
    assert screen.cat is None and screen.covered
    assert screen.cover.payload["hash"] == overlay.load_keyword(tmp_path)
    assert no_lock == []  # covered, not OS-locked


def test_no_keyword_falls_back_to_os_lock(tmp_path, no_lock):
    cfg = cfg_for(tmp_path)
    screen = overlay.ScreenOverlay(tmp_path, spawn=FakeChild)
    app.perform(cfg, Action(LOCK, "l"), None, screen)
    assert not screen.covered and no_lock == [False]


def test_os_mode_never_covers(tmp_path, no_lock):
    overlay.save_keyword(tmp_path, "kitty")
    cfg = cfg_for(tmp_path, "cover.on_lock='os'", "cover.cat_on_warn=false")
    screen = overlay.ScreenOverlay(tmp_path, spawn=FakeChild)
    app.perform(cfg, Action(WARN, "w"), None, screen)
    app.perform(cfg, Action(LOCK, "l"), None, screen)
    assert screen.cat is None and not screen.covered and no_lock == [False]


@pytest.mark.parametrize("rc, locks", [(overlay.EXIT_UNLOCKED, []), (overlay.EXIT_ABORTED, [False]),
                                       (-15, [False])])
def test_cover_closed(tmp_path, no_lock, rc, locks):
    overlay.save_keyword(tmp_path, "kitty")
    cfg = cfg_for(tmp_path)
    screen = overlay.ScreenOverlay(tmp_path, spawn=FakeChild)
    screen.cover_screen()
    assert screen.cover_result() is None  # still up
    screen.cover.exit(rc)
    got = screen.cover_result()
    assert got == rc and not screen.covered
    policy = Policy(1, 5, 0.8, 15, 0)
    app.cover_closed(cfg, got, policy, 100.0)
    assert no_lock == locks  # only the keyword avoids the real lock
    assert policy.in_cooldown(114.0) and not policy.in_cooldown(115.0)


def test_bad_cover_mode(tmp_path):
    with pytest.raises(config.ConfigError, match="cover.on_lock"):
        cfg_for(tmp_path, "cover.on_lock='nope'")
