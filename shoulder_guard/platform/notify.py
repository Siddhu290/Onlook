"""Desktop notifications and a beep. Always logs; never raises."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger("shoulder_guard")
APP = "ShoulderGuard"
OSASCRIPT = ['on run argv', 'display notification (item 2 of argv) with title (item 1 of argv)',
             'end run']
PLYER_TIMEOUT = 4


def command(title: str, msg: str, platform: str = sys.platform) -> list[str] | None:
    if platform.startswith("linux"):
        return ["notify-send", "-u", "critical", "-a", APP, title, msg]
    if platform == "darwin":  # message passed as argv: quotes in it can't break the script
        return ["osascript", *sum((["-e", line] for line in OSASCRIPT), []), title, msg]
    return None


def available(platform: str = sys.platform) -> bool:
    cmd = command("", "", platform)
    if cmd:
        return shutil.which(cmd[0]) is not None
    try:
        from plyer import notification  # noqa: F401
        return True
    except Exception:
        return False


def notify(title: str, msg: str, enabled: bool = True) -> None:
    log.warning("%s: %s", title, msg)
    if not enabled:
        return
    try:
        cmd = command(title, msg)
        if cmd:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            from plyer import notification
            notification.notify(title=title, message=msg, app_name=APP, timeout=PLYER_TIMEOUT)
    except Exception as e:
        log.debug("notification failed: %s", e)


def beep(enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif sys.stdout is not None:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception as e:
        log.debug("beep failed: %s", e)
