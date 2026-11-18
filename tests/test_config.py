import pytest

from shoulder_guard import config


def test_creates_user_file_from_defaults(tmp_path):
    p = tmp_path / "sub" / "config.toml"
    cfg = config.load(p)
    assert p.read_text() == config.DEFAULT_TEXT
    assert cfg.timing.warn_after == 1.0 and cfg.identity.match_threshold == 0.38
    assert cfg.actions.lock is True and cfg.camera.index == 0


def test_user_file_and_overrides(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[timing]\nlock_after = 8\n[camera]\nindex = 2\n")
    cfg = config.load(p, ["camera.index=1", "actions.dry_run=true", "paths.home='/x'"])
    assert cfg.timing.lock_after == 8.0 and isinstance(cfg.timing.lock_after, float)
    assert cfg.camera.index == 1 and cfg.actions.dry_run is True
    assert cfg.paths.home == "/x"


@pytest.mark.parametrize("text, msg", [
    ("[timing]\nfps = 0\n", "timing.fps must be between"),
    ("[camera]\nindex = 1.5\n", "camera.index must be int"),
    ("[actions]\nlock = 1\n", "actions.lock must be bool"),
    ("[timing]\nwarn_after = 6\n", "lock_after"),
    ("[detection]\npitch_min = 2\n", "pitch_min must be <"),
    ("[timing]\ntypo = 1\n", "unknown config key timing.typo"),
    ("[nope]\nx = 1\n", "unknown config section"),
    ("[timing\n", "c.toml"),
])
def test_bad_values_fail_clearly(tmp_path, text, msg):
    p = tmp_path / "c.toml"
    p.write_text(text)
    with pytest.raises(config.ConfigError, match=msg):
        config.load(p)


def test_bad_override_syntax(tmp_path):
    with pytest.raises(config.ConfigError, match="section.key=value"):
        config.load(tmp_path / "c.toml", ["fps=3"])
