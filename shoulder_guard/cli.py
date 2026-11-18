"""Command line: run, enroll, doctor, install-autostart, uninstall-autostart."""
from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import sys

from shoulder_guard import __version__, config

log = logging.getLogger("shoulder_guard")


def setup_logging(cfg) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.handlers.RotatingFileHandler(
        cfg.home / "shoulder_guard.log", maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    log.handlers.clear()
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(cfg.logging.level.upper())
    log.propagate = False


def set_keyword(cfg) -> int:
    from shoulder_guard import overlay
    first = getpass.getpass("New unblur keyword (hidden): ")
    if len(first) < overlay.MIN_KEYWORD_LEN or len(first) > overlay.MAX_KEYWORD_LEN:
        print(f"Keyword must be {overlay.MIN_KEYWORD_LEN}-{overlay.MAX_KEYWORD_LEN} characters.",
              file=sys.stderr)
        return 1
    if getpass.getpass("Type it again: ") != first:
        print("They don't match; nothing changed.", file=sys.stderr)
        return 1
    print(f"Saved (salted hash only) to {overlay.save_keyword(cfg.home, first)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help=f"config file (default {config.DEFAULT_PATH})")
    common.add_argument("--set", dest="overrides", action="append", default=[],
                        metavar="SECTION.KEY=VALUE",
                        help="override a config value, e.g. --set timing.lock_after=8 (repeatable)")

    ap = argparse.ArgumentParser(prog="shoulder-guard",
                                 description="Lock your PC when someone else reads your screen.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", parents=[common], help="watch the webcam (default use)")
    run.add_argument("--preview", action="store_true", help="show a debug window (q quits)")
    run.add_argument("--dry-run", action="store_true", help="never lock, just log 'would lock'")
    run.add_argument("--no-tray", action="store_true", help="don't show the tray icon")

    enroll = sub.add_parser("enroll", parents=[common], help="teach it your face")
    enroll.add_argument("--append", action="store_true",
                        help="add samples to the existing gallery (other lighting, glasses)")

    doc = sub.add_parser("doctor", parents=[common], help="check camera, models, lock, ...")
    doc.add_argument("--test-lock", action="store_true", help="REALLY lock the screen once")
    doc.add_argument("--no-camera", action="store_true", help="skip the webcam check")

    sub.add_parser("set-keyword", parents=[common],
                   help="set the keyword that removes the blur cover")

    for name, text in (("install-autostart", "start ShoulderGuard at login"),
                       ("uninstall-autostart", "remove the login entry")):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("--dir", help="write the entry here instead of the OS location (testing)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = list(args.overrides)
    if getattr(args, "dry_run", False):
        overrides.append("actions.dry_run=true")
    try:
        cfg = config.load(args.config, overrides)
    except config.ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg)

    if args.cmd == "set-keyword":
        return set_keyword(cfg)
    if args.cmd in ("install-autostart", "uninstall-autostart"):
        from shoulder_guard.platform import autostart
        if args.cmd == "install-autostart":
            print(f"Installed autostart entry: {autostart.install(args.dir)}")
        else:
            path = autostart.uninstall(args.dir)
            print(f"Removed {path}" if path else "No autostart entry found.")
        return 0

    from shoulder_guard import app
    try:
        if args.cmd == "run":
            return app.run(cfg, preview=args.preview, use_tray=not args.no_tray)
        if args.cmd == "enroll":
            return app.enroll(cfg, append=args.append)
        return app.doctor(cfg, test_lock=args.test_lock, check_camera=not args.no_camera)
    except app.UserError as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
