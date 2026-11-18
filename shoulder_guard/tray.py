"""Optional system-tray icon (pystray + Pillow). ShoulderGuard runs fine without it."""
from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger("shoulder_guard")

ICON_SIZE = 64
DARK = (32, 26, 20)
# cat face on a 64px canvas: ears, head, eyes, nose
CAT_EARS = [((8, 32), (12, 3), (31, 16)), ((56, 32), (52, 3), (33, 16))]
CAT_HEAD = (5, 11, 59, 61)
CAT_EYES = [(18, 28, 27, 41), (37, 28, 46, 41)]
CAT_NOSE = ((28, 45), (36, 45), (32, 50))
REFRESH_SECONDS = 0.5
STATUS = {  # status -> (RGB colour, tooltip)
    "idle": ((46, 160, 67), "ShoulderGuard: on guard"),
    "watching": ((220, 38, 38), "ShoulderGuard: someone is WATCHING"),
    "paused": ((140, 140, 140), "ShoulderGuard: paused"),
    "camera": ((245, 130, 32), "ShoulderGuard: camera unavailable"),
    "covered": ((220, 38, 38), "ShoulderGuard: screen covered (type your keyword)"),
}


def open_path(path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    except Exception as e:
        log.error("Could not open %s: %s", path, e)


class Tray:
    def __init__(self, icon):
        self.icon = icon

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception as e:
            log.debug("tray stop: %s", e)


def start_tray(state, cfg) -> Tray | None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as e:
        log.info("Tray icon disabled (%s). Install with: pip install 'shoulder-guard[tray]'", e)
        return None

    def image(status):
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        d, color = ImageDraw.Draw(img), STATUS[status][0]
        for ear in CAT_EARS:
            d.polygon(ear, fill=color)
        d.ellipse(CAT_HEAD, fill=color)
        for eye in CAT_EYES:
            d.ellipse(eye, fill=DARK)
        d.polygon(CAT_NOSE, fill=DARK)
        return img

    def pause(seconds):
        state.pause_until = time.monotonic() + seconds

    def resume():
        state.pause_until = 0.0

    def quit_(icon):
        state.stop.set()
        icon.stop()

    # pystray logs a traceback every time the tray host disappears (e.g. GNOME screen lock).
    # The icon is cosmetic, so keep those out of the console.
    logging.getLogger("pystray").setLevel(logging.CRITICAL)

    minutes = cfg.tray.pause_minutes
    menu = pystray.Menu(
        pystray.MenuItem(lambda item: STATUS[state.status][1], None, enabled=False),
        pystray.MenuItem(f"Pause {minutes:g} min", lambda: pause(minutes * 60)),
        pystray.MenuItem("Pause until resumed", lambda: pause(math.inf)),
        pystray.MenuItem("Resume", resume),
        pystray.MenuItem("Open config", lambda: open_path(cfg.config_path)),
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("shoulder_guard", image("idle"), STATUS["idle"][1], menu)
    def serve():
        try:
            icon.run()
        except Exception as e:
            log.warning("Tray icon unavailable (%s); continuing without it.", e)

    # ponytail: a daemon thread instead of run_detached(), whose X11 thread blocked exit.
    # macOS needs the tray on the main thread, so there the tray may not show (README).
    threading.Thread(target=serve, name="tray", daemon=True).start()

    def sync():
        shown = "idle"
        while not state.stop.wait(REFRESH_SECONDS):
            # ponytail: private attr of pystray's X11 backend. It goes None when the tray host
            # dies (GNOME screen lock) and pystray only re-docks on a redraw, so we redraw
            # until it's back. Proper fix: AppIndicator backend (needs apt gir1.2-ayatana...).
            undocked = getattr(icon, "_systray_manager", True) is None
            if state.status != shown or undocked:
                shown = state.status
                icon.icon, icon.title = image(shown), STATUS[shown][1]
                icon.update_menu()
    threading.Thread(target=sync, name="tray-sync", daemon=True).start()
    log.info("Tray icon started (%s backend).", type(icon).__module__.rsplit(".", 1)[-1])
    return Tray(icon)
