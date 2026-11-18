"""Lock the screen. Windows: LockWorkStation. macOS: display sleep (needs
'Require password immediately' in System Settings > Lock Screen). Linux: see LINUX_ORDER."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("shoulder_guard")

GNOME_DBUS = ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
              "--object-path", "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.Lock"]
GNOME_INTROSPECT = ["gdbus", "introspect", "--session", "--dest", "org.gnome.ScreenSaver",
                    "--object-path", "/org/gnome/ScreenSaver"]
FALLBACKS = [["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"], ["dm-tool", "lock"]]


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def session_id(timeout: float, env=os.environ, run=_run) -> str | None:
    """XDG_SESSION_ID, else this user's session from `loginctl list-sessions`,
    preferring one with a seat (the graphical login)."""
    if env.get("XDG_SESSION_ID"):
        return env["XDG_SESSION_ID"]
    try:
        out = run(["loginctl", "list-sessions", "--no-legend"], timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    mine = [line.split() for line in out.splitlines() if len(line.split()) >= 3]
    mine = [c for c in mine if c[1] == uid]
    seated = [c for c in mine if len(c) >= 4 and c[3].startswith("seat")]
    return (seated or mine or [[None]])[0][0]


def linux_commands(timeout: float, env=os.environ, run=_run) -> list[list[str]]:
    cmds = [GNOME_DBUS]
    sid = session_id(timeout, env, run)
    cmds.append(["loginctl", "lock-session", sid] if sid else ["loginctl", "lock-session"])
    return cmds + FALLBACKS


def available_methods(timeout: float, run=_run) -> list[str]:
    """What could lock this machine, checked WITHOUT locking."""
    if sys.platform == "win32":
        return ["LockWorkStation"]
    if sys.platform == "darwin":
        return ["pmset displaysleepnow"] if shutil.which("pmset") else []
    found = []
    if shutil.which("gdbus"):
        try:
            if "Lock()" in run(GNOME_INTROSPECT, timeout).stdout:
                found.append("GNOME D-Bus Lock")
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("loginctl"):
        sid = session_id(timeout, run=run)
        found.append(f"loginctl lock-session {sid}" if sid else "loginctl (no session id)")
    found += [" ".join(c) for c in FALLBACKS if shutil.which(c[0])]
    return found


def lock_screen(dry_run: bool, timeout: float, run=_run) -> bool:
    if dry_run:
        log.warning("DRY RUN: would lock the screen now.")
        return True
    try:
        if sys.platform == "win32":
            import ctypes
            return bool(ctypes.windll.user32.LockWorkStation())
        cmds = [["pmset", "displaysleepnow"]] if sys.platform == "darwin" else \
            linux_commands(timeout, run=run)
    except Exception as e:
        log.error("Could not lock the screen: %s", e)
        return False
    for cmd in cmds:
        try:
            r = run(cmd, timeout)
            if r.returncode == 0:
                log.warning("Screen locked via: %s", " ".join(cmd))
                return True
            log.debug("%s failed: %s", cmd[0], r.stderr.strip())
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("%s unavailable: %s", cmd[0], e)
    log.error("Could not lock the screen: none of %s worked. Run `shoulder-guard doctor`.",
              ", ".join(c[0] for c in cmds))
    return False
