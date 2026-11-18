"""Cat badge ("Someone is watching!") and a keyword-protected frosted screen cover.

The windows run in a child process (`python -m shoulder_guard.overlay cat|cover`) so the
GTK main loop never touches the camera loop or OpenCV's own GUI. The cover child exits
with EXIT_UNLOCKED only when the right keyword is typed; any other exit means the app
falls back to the real OS lock.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("shoulder_guard")

KEYWORD_FILE = "keyword"
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
MIN_KEYWORD_LEN = 3
MAX_KEYWORD_LEN = 128
SYSTEM_SITE = "/usr/lib/python3/dist-packages"  # where Debian/Ubuntu keep python3-gi
CHILD_ENV = {"GDK_BACKEND": "x11"}  # XWayland: windows may stay on top and be positioned
EXIT_UNLOCKED, EXIT_ABORTED = 0, 3
CHECK_TIMEOUT = 10
CLOSE_TIMEOUT = 2

CAT_W, CAT_H, CAT_BOTTOM_MARGIN = 160, 140, 60  # margin clears a bottom dock/taskbar
WRONG_PAUSE_MS = 1000  # input ignored this long after a wrong keyword (slows guessing)
REFOCUS_MS = 150
MAX_DOTS = 32

ORANGE, DARK, PINK, WHITE = (0.96, 0.62, 0.25), (0.13, 0.1, 0.08), (1.0, 0.62, 0.72), \
    (1.0, 1.0, 1.0)
ALERT = (1.0, 0.42, 0.42)
COVER_TOP, COVER_BOTTOM = (0.16, 0.19, 0.33), (0.33, 0.22, 0.4)
# (x, y, radius) as fractions of the screen, (r, g, b, a): soft blobs that read as "blurred"
BLOBS = [((0.2, 0.25, 0.45), (0.45, 0.55, 0.95, 0.35)), ((0.8, 0.3, 0.4), (0.95, 0.5, 0.7, 0.3)),
         ((0.55, 0.8, 0.5), (0.4, 0.8, 0.75, 0.25)), ((0.1, 0.85, 0.35), (0.95, 0.75, 0.4, 0.2))]


# ------------------------------------------------------------------ keyword

def _digest(keyword: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", keyword.encode(), salt, iterations).hex()


def hash_keyword(keyword: str) -> str:
    """'pbkdf2_sha256$iterations$salt$digest$length'. The length lets the cover unlock the
    moment the last typed characters match, without Enter (it reveals only the length)."""
    salt = secrets.token_bytes(SALT_BYTES)
    return (f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}$"
            f"{_digest(keyword, salt, PBKDF2_ITERATIONS)}${len(keyword)}")


def check_keyword(keyword: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) not in (4, 5):  # 4 = older hashes without the length
        return False
    try:
        got = _digest(keyword, bytes.fromhex(parts[2]), int(parts[1]))
    except ValueError:
        return False
    return hmac.compare_digest(got, parts[3])


def keyword_length(stored: str) -> int | None:
    parts = stored.split("$")
    return int(parts[4]) if len(parts) == 5 and parts[4].isdigit() else None


def typed_unlocks(typed: str, stored: str) -> bool:
    """True when the last characters typed are the keyword (no Enter needed)."""
    n = keyword_length(stored)
    return n is not None and len(typed) >= n and check_keyword(typed[-n:], stored)


def save_keyword(home: Path, keyword: str) -> Path:
    path = Path(home) / KEYWORD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(hash_keyword(keyword) + "\n")
    os.chmod(path, 0o600)
    return path


def load_keyword(home: Path) -> str | None:
    path = Path(home) / KEYWORD_FILE
    return path.read_text().strip() or None if path.exists() else None


# ------------------------------------------------------------- parent side

class Overlay:
    """One overlay child process."""

    def __init__(self, kind: str, payload: dict | None = None):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "shoulder_guard.overlay", kind], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, **CHILD_ENV})
        self.proc.stdin.write(json.dumps(payload or {}).encode())
        self.proc.stdin.close()

    def running(self) -> bool:
        return self.proc.poll() is None

    @property
    def returncode(self):
        return self.proc.returncode

    def close(self) -> None:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(CLOSE_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class ScreenOverlay:
    """What the main loop talks to: show/hide the cat, cover the screen, poll the cover."""

    def __init__(self, home: Path, spawn=Overlay):
        self.home, self.spawn = Path(home), spawn
        self.cat: Overlay | None = None
        self.cover: Overlay | None = None

    def show_cat(self) -> None:
        if self.cat is None or not self.cat.running():
            try:
                self.cat = self.spawn("cat")
            except OSError as e:
                log.error("Cat overlay failed: %s", e)

    def hide_cat(self) -> None:
        if self.cat is not None:
            self.cat.close()
            self.cat = None

    def cover_screen(self) -> bool:
        """Start the cover. False = couldn't (caller should use the OS lock)."""
        self.hide_cat()
        stored = load_keyword(self.home)
        if not stored:
            log.error("No unblur keyword set (run `shoulder-guard set-keyword`).")
            return False
        try:
            self.cover = self.spawn("cover", {"hash": stored})
        except OSError as e:
            log.error("Screen cover failed: %s", e)
            return False
        return True

    @property
    def covered(self) -> bool:
        return self.cover is not None

    def cover_result(self):
        """None while the cover is up (or there is none); its exit code once it closed."""
        if self.cover is None or self.cover.running():
            return None
        rc, self.cover = self.cover.returncode, None
        return rc


def available() -> tuple[bool, str]:
    """Can the overlay child start GTK here? Checked in a child, without showing anything."""
    try:
        r = subprocess.run([sys.executable, "-m", "shoulder_guard.overlay", "check"],
                           capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                           env={**os.environ, **CHILD_ENV})
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    lines = (r.stderr or "").strip().splitlines()
    return r.returncode == 0, "GTK 3" if r.returncode == 0 else (lines or ["failed"])[-1]


# ----------------------------------------------------------------- drawing

def draw_cat(cr, cx: float, cy: float, r: float) -> None:
    """A round orange cat whose eyes glance sideways at your screen."""
    for side in (-1, 1):
        for color, k in ((ORANGE, 1.0), (PINK, 0.55)):
            cr.move_to(cx + side * 0.9 * r * k, cy - 0.25 * r)
            cr.line_to(cx + side * 0.78 * r, cy - 0.75 * r - 0.5 * r * k)
            cr.line_to(cx + side * (0.78 - 0.55 * k) * r, cy - 0.78 * r)
            cr.close_path()
            cr.set_source_rgb(*color)
            cr.fill()
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.set_source_rgb(*ORANGE)
    cr.fill()
    for side in (-1, 1):
        ex, ey = cx + side * 0.4 * r, cy - 0.1 * r
        for color, dx, sx, sy in ((WHITE, 0, 0.22, 0.28), (DARK, 0.08, 0.08, 0.2)):
            cr.save()
            cr.translate(ex + dx * r, ey)
            cr.scale(sx * r, sy * r)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
            cr.set_source_rgb(*color)
            cr.fill()
    cr.move_to(cx - 0.1 * r, cy + 0.25 * r)
    cr.line_to(cx + 0.1 * r, cy + 0.25 * r)
    cr.line_to(cx, cy + 0.37 * r)
    cr.close_path()
    cr.set_source_rgb(*PINK)
    cr.fill()
    cr.set_source_rgb(*DARK)
    cr.set_line_width(max(1.0, 0.04 * r))
    for side in (-1, 1):
        cr.new_sub_path()
        cr.arc(cx + side * 0.12 * r, cy + 0.4 * r, 0.12 * r, 0, math.pi)
        cr.stroke()
        for dy in (-0.08, 0.05, 0.18):
            cr.move_to(cx + side * 0.45 * r, cy + (0.3 + dy) * r)
            cr.line_to(cx + side * 1.3 * r, cy + (0.25 + 2 * dy) * r)
            cr.stroke()


def draw_text(cr, text: str, cx: float, y: float, size: float, rgb) -> None:
    cr.select_font_face("Sans", 0, 1)  # normal slant, bold
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.move_to(cx - ext.width / 2 - ext.x_bearing, y)
    cr.set_source_rgb(*rgb)
    cr.show_text(text)


def draw_badge(cr, w: float, h: float, transparent: bool) -> None:
    if transparent:
        cr.set_operator(1)  # SOURCE: clear to fully transparent
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)  # OVER
    radius = h * 0.08
    cr.new_sub_path()
    for x, y, a in ((w - radius, radius, -0.5), (w - radius, h - radius, 0),
                    (radius, h - radius, 0.5), (radius, radius, 1)):
        cr.arc(x, y, radius, a * math.pi, (a + 0.5) * math.pi)
    cr.close_path()
    cr.set_source_rgba(0.1, 0.1, 0.13, 0.92)
    cr.fill()
    draw_cat(cr, w / 2, h * 0.45, h * 0.24)
    draw_text(cr, "Someone is watching!", w / 2, h * 0.9, h * 0.08, ALERT)


def draw_cover(cr, w: float, h: float, typed: int, wrong: bool) -> None:
    import cairo

    grad = cairo.LinearGradient(0, 0, w, h)
    grad.add_color_stop_rgb(0, *COVER_TOP)
    grad.add_color_stop_rgb(1, *COVER_BOTTOM)
    cr.set_source(grad)
    cr.paint()
    for (fx, fy, fr), (r, g, b, a) in BLOBS:
        x, y, rad = fx * w, fy * h, fr * max(w, h)
        blob = cairo.RadialGradient(x, y, 0, x, y, rad)
        blob.add_color_stop_rgba(0, r, g, b, a)
        blob.add_color_stop_rgba(1, r, g, b, 0)
        cr.set_source(blob)
        cr.paint()
    cx, unit = w / 2, min(w, h)
    draw_cat(cr, cx, h * 0.36, unit * 0.12)
    draw_text(cr, "Someone was watching your screen", cx, h * 0.6, unit * 0.04, WHITE)
    draw_text(cr, "Type your keyword", cx, h * 0.66, unit * 0.025, WHITE)
    dots = "● " * min(typed, MAX_DOTS)
    draw_text(cr, dots.strip() or "_", cx, h * 0.74, unit * 0.03, WHITE)
    if wrong:
        draw_text(cr, "Wrong keyword", cx, h * 0.8, unit * 0.028, ALERT)


# -------------------------------------------------------------- child side

def _gtk():
    try:
        import gi
    except ImportError:  # venv without PyGObject: borrow the system's python3-gi
        if os.path.isdir(SYSTEM_SITE):
            sys.path.append(SYSTEM_SITE)
        import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    return Gdk, GLib, Gtk


def _run_cat(Gdk, GLib, Gtk) -> int:
    win = Gtk.Window(type=Gtk.WindowType.POPUP)  # override-redirect: on top, never focused
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    transparent = visual is not None and screen.is_composited()
    if transparent:
        win.set_visual(visual)
    win.set_app_paintable(True)
    win.set_default_size(CAT_W, CAT_H)
    display = Gdk.Display.get_default()
    geo = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
    win.move(geo.x + (geo.width - CAT_W) // 2, geo.y + geo.height - CAT_H - CAT_BOTTOM_MARGIN)
    win.connect("draw", lambda w, cr: draw_badge(cr, CAT_W, CAT_H, transparent))
    win.show_all()
    Gtk.main()
    return 0


def _run_cover(Gdk, GLib, Gtk, stored: str) -> int:
    st = {"typed": "", "wrong": False, "until": 0.0, "ok": False}
    display, wins = Gdk.Display.get_default(), []

    def redraw():
        for w in wins:
            w.queue_draw()

    def clear_wrong():
        st["wrong"] = False
        redraw()
        return False

    def on_key(_w, ev):
        if time.monotonic() < st["until"]:
            return True
        k = ev.keyval
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if check_keyword(st["typed"], stored):
                st["ok"] = True
                Gtk.main_quit()
                return True
            st.update(typed="", wrong=True, until=time.monotonic() + WRONG_PAUSE_MS / 1000)
            GLib.timeout_add(WRONG_PAUSE_MS, clear_wrong)
        elif k == Gdk.KEY_BackSpace:
            st["typed"] = st["typed"][:-1]
        elif k == Gdk.KEY_Escape:
            st["typed"] = ""
        else:
            ch = Gdk.keyval_to_unicode(k)
            if ch >= 32:
                st["typed"] = (st["typed"] + chr(ch))[-MAX_KEYWORD_LEN:]
                if typed_unlocks(st["typed"], stored):
                    st["ok"] = True
                    Gtk.main_quit()
                    return True
        redraw()
        return True

    def refocus():
        wins[0].present()
        try:  # works for XWayland only while we hold focus; best effort
            display.get_default_seat().grab(wins[0].get_window(), Gdk.SeatCapabilities.KEYBOARD,
                                            False, None, None, None, None)
        except Exception:
            pass
        return False

    screen = Gdk.Screen.get_default()
    for i in range(display.get_n_monitors()):
        geo = display.get_monitor(i).get_geometry()
        w = Gtk.Window()
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.move(geo.x, geo.y)
        w.set_default_size(geo.width, geo.height)
        w.connect("delete-event", lambda *a: True)  # Alt+F4 does nothing
        w.connect("key-press-event", on_key)
        w.connect("focus-out-event", lambda *a: GLib.timeout_add(REFOCUS_MS, refocus) and False)
        w.connect("draw", lambda w, cr: draw_cover(cr, w.get_allocated_width(),
                                                   w.get_allocated_height(),
                                                   len(st["typed"]), st["wrong"]))
        w.show_all()
        w.fullscreen_on_monitor(screen, i)
        wins.append(w)
    GLib.timeout_add(REFOCUS_MS, refocus)
    Gtk.main()
    return EXIT_UNLOCKED if st["ok"] else EXIT_ABORTED


def _render(out_dir: str) -> int:
    """Draw the badge and the cover into PNGs (no display needed). For checking the art."""
    _gtk()
    import cairo
    for name, (w, h), fn in (("cat.png", (CAT_W, CAT_H), lambda cr: draw_badge(cr, CAT_W, CAT_H,
                                                                                True)),
                             ("cover.png", (1280, 720), lambda cr: draw_cover(cr, 1280, 720, 5,
                                                                              True))):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        fn(cairo.Context(surface))
        surface.write_to_png(str(Path(out_dir) / name))
    return 0


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else ""
    if kind == "render":
        return _render(argv[1])
    Gdk, GLib, Gtk = _gtk()
    if kind == "check":
        return 0
    payload = json.loads(sys.stdin.read() or "{}")
    if kind == "cat":
        return _run_cat(Gdk, GLib, Gtk)
    if kind == "cover":
        return _run_cover(Gdk, GLib, Gtk, payload["hash"])
    print(f"unknown overlay kind {kind!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
