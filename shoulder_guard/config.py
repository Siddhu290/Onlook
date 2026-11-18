"""Load, override and validate the TOML config."""
from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_TEXT = resources.files(__package__).joinpath("default_config.toml").read_text("utf-8")
DEFAULTS = tomllib.loads(DEFAULT_TEXT)
DEFAULT_PATH = Path("~/.shoulder_guard/config.toml").expanduser()
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
COVER_MODES = ("blur", "os")

INF = float("inf")
# (section, key): (type, min, max). Bounds are inclusive; type float also accepts ints.
RULES = {
    ("timing", "warn_after"): (float, 0, INF),
    ("timing", "lock_after"): (float, 0, INF),
    ("timing", "grace"): (float, 0, INF),
    ("timing", "cooldown"): (float, 0, INF),
    ("timing", "away_lock"): (float, 0, INF),
    ("timing", "fps"): (float, 0.1, 60),
    ("detection", "score_threshold"): (float, 0, 1),
    ("detection", "nms_threshold"): (float, 0, 1),
    ("detection", "top_k"): (int, 1, 10000),
    ("detection", "min_face_px"): (int, 1, 10000),
    ("detection", "max_yaw"): (float, 0, 10),
    ("detection", "pitch_min"): (float, -10, 10),
    ("detection", "pitch_max"): (float, -10, 10),
    ("identity", "match_threshold"): (float, -1, 1),
    ("identity", "enroll_samples"): (int, 1, 10000),
    ("identity", "min_enroll_samples"): (int, 1, 10000),
    ("identity", "enroll_interval"): (float, 0, INF),
    ("identity", "enroll_timeout"): (float, 1, INF),
    ("identity", "owner_votes_window"): (int, 1, 1000),
    ("identity", "look_votes_window"): (int, 1, 1000),
    ("identity", "track_ttl"): (float, 0.01, INF),
    ("identity", "iou_match"): (float, 0, 1),
    ("camera", "index"): (int, 0, 1000),
    ("camera", "width"): (int, 1, 10000),
    ("camera", "height"): (int, 1, 10000),
    ("camera", "reconnect_after_failures"): (int, 1, 100000),
    ("camera", "retry_seconds"): (float, 0, INF),
    ("actions", "notify"): (bool, None, None),
    ("actions", "beep"): (bool, None, None),
    ("actions", "lock"): (bool, None, None),
    ("actions", "snapshot_on_lock"): (bool, None, None),
    ("actions", "dry_run"): (bool, None, None),
    ("actions", "command_timeout"): (float, 0.1, 120),
    ("cover", "on_lock"): (str, None, None),
    ("cover", "cat_on_warn"): (bool, None, None),
    ("logging", "level"): (str, None, None),
    ("logging", "max_bytes"): (int, 1000, 10**9),
    ("logging", "backup_count"): (int, 0, 100),
    ("tray", "pause_minutes"): (float, 0.1, 24 * 60),
    ("paths", "home"): (str, None, None),
}


class ConfigError(ValueError):
    pass


def parse_override(item: str) -> tuple[str, str, object]:
    """'timing.lock_after=8' -> ('timing', 'lock_after', 8). Values use TOML syntax;
    anything that isn't valid TOML is taken as a plain string."""
    key, sep, raw = item.partition("=")
    section, dot, name = key.strip().partition(".")
    if not sep or not dot:
        raise ConfigError(f"bad override {item!r}, expected section.key=value")
    try:
        value = tomllib.loads(f"v = {raw.strip()}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw.strip()
    return section, name, value


def validate(data: dict) -> None:
    for section, keys in data.items():
        if section not in DEFAULTS or not isinstance(keys, dict):
            raise ConfigError(f"unknown config section [{section}]")
        for name, value in keys.items():
            rule = RULES.get((section, name))
            if rule is None:
                raise ConfigError(f"unknown config key {section}.{name}")
            typ, lo, hi = rule
            where = f"{section}.{name}"
            ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  if typ is float else
                  isinstance(value, typ) and not (typ is int and isinstance(value, bool)))
            if not ok:
                raise ConfigError(f"{where} must be {typ.__name__}, got {value!r}")
            if lo is not None and not lo <= value <= hi:
                raise ConfigError(f"{where} must be between {lo} and {hi}, got {value!r}")
    t, d, i = data["timing"], data["detection"], data["identity"]
    if t["lock_after"] <= t["warn_after"]:
        raise ConfigError(f"timing.lock_after ({t['lock_after']}) must be > "
                          f"timing.warn_after ({t['warn_after']})")
    if d["pitch_min"] >= d["pitch_max"]:
        raise ConfigError("detection.pitch_min must be < detection.pitch_max")
    if i["enroll_samples"] < i["min_enroll_samples"]:
        raise ConfigError("identity.enroll_samples must be >= identity.min_enroll_samples")
    if data["cover"]["on_lock"] not in COVER_MODES:
        raise ConfigError(f"cover.on_lock must be one of {COVER_MODES}")
    if data["logging"]["level"].upper() not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {LOG_LEVELS}")


def load(path: Path | None = None, overrides: list[str] = ()) -> SimpleNamespace:
    """Defaults <- user file (created from defaults if missing) <- CLI overrides."""
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TEXT, "utf-8")
    try:
        user = tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e

    merged = {s: dict(v) for s, v in DEFAULTS.items()}
    for section, keys in user.items():
        if not isinstance(keys, dict):
            raise ConfigError(f"{path}: '{section}' must be a [section]")
        merged.setdefault(section, {}).update(keys)
    for item in overrides:
        section, name, value = parse_override(item)
        merged.setdefault(section, {})[name] = value
    validate(merged)

    for k, v in merged["timing"].items():  # ints are fine in TOML; normalise floats
        merged["timing"][k] = float(v)
    merged["paths"]["home"] = str(Path(merged["paths"]["home"]).expanduser())
    cfg = SimpleNamespace(**{s: SimpleNamespace(**v) for s, v in merged.items()})
    cfg.config_path = path
    cfg.home = Path(cfg.paths.home)
    return cfg
