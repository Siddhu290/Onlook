"""Wires vision -> tracker -> policy -> actions. Also: enroll and doctor."""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field

import cv2

from shoulder_guard import models, overlay
from shoulder_guard.platform import lock, notify
from shoulder_guard.platform.camera import Camera, open_capture
from shoulder_guard.policy import LOCK, WARN, Policy
from shoulder_guard.vision.detector import Detector
from shoulder_guard.vision.pose import head_pose
from shoulder_guard.vision.recognizer import Recognizer, load_gallery, save_gallery, similarity
from shoulder_guard.vision.tracker import Tracker, biggest_index

log = logging.getLogger("shoulder_guard")

# BGR colours for the preview / enrollment windows
GREEN, RED, ORANGE, WHITE, YELLOW = (0, 200, 0), (0, 0, 255), (0, 165, 255), (255, 255, 255), \
    (0, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX
RUN_WINDOW = "ShoulderGuard (q to quit)"
ENROLL_WINDOW = "ShoulderGuard - enrollment (q to abort)"
QUIT_KEY = ord("q")
TEST_LOCK_DELAY = 3  # seconds of warning before `doctor --test-lock` locks


class UserError(RuntimeError):
    """Something the user can fix; printed without a traceback."""


@dataclass
class State:
    """Shared between the main loop and the tray thread."""
    status: str = "idle"  # idle | watching | paused | camera
    pause_until: float = 0.0  # monotonic time; math.inf = until resumed
    stop: threading.Event = field(default_factory=threading.Event)

    def paused(self, now: float) -> bool:
        return now < self.pause_until


def gui_available() -> bool:
    """False on opencv-python-headless, where imshow crashes."""
    m = re.search(r"GUI:\s*(\S+)", cv2.getBuildInformation())
    return bool(m) and m.group(1).upper() != "NONE"


def load_engine(cfg) -> tuple[Detector, Recognizer]:
    d, models_dir = cfg.detection, cfg.home / "models"
    try:
        yunet, sface = models.ensure("yunet", models_dir), models.ensure("sface", models_dir)
    except models.ModelError as e:
        raise UserError(str(e)) from e
    return (Detector(yunet, d.score_threshold, d.nms_threshold, d.top_k, d.min_face_px),
            Recognizer(sface))


def load_owner(cfg):
    try:
        return load_gallery(cfg.home / "owner.npy")
    except ValueError as e:
        raise UserError(str(e)) from e


# ----------------------------------------------------------------------- run

def analyse(frame, now, faces, embed, tracker, gallery, cfg):
    """Pose + identity votes for every detected face. Returns tracks aligned with faces."""
    d, match = cfg.detection, cfg.identity.match_threshold
    boxes = [tuple(map(float, f[:4])) for f in faces]
    tracks = tracker.update(boxes, now)
    biggest = biggest_index(boxes)
    for i, (f, t) in enumerate(zip(faces, tracks)):
        t.yaw, t.pitch = head_pose(f)
        t.look_votes.append(abs(t.yaw) <= d.max_yaw and d.pitch_min <= t.pitch <= d.pitch_max)
        if gallery is not None:
            t.sim = similarity(gallery, embed(frame, f))
            t.owner_votes.append(t.sim >= match)
        else:  # fallback: the biggest (closest) face is assumed to be the owner
            t.owner_votes.append(i == biggest)
    return tracks


def perform(cfg, action, frame, screen=None) -> None:
    a = cfg.actions
    if action.kind == WARN:
        notify.notify("Someone is looking at your screen", action.reason, a.notify)
        notify.beep(a.beep)
        if screen and cfg.cover.cat_on_warn:
            screen.show_cat()
        return
    if action.kind == LOCK and a.snapshot_on_lock and frame is not None:
        snap = cfg.home / "snapshots" / time.strftime("%Y%m%d-%H%M%S.jpg")
        snap.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(snap), frame)
        log.info("Snapshot saved: %s", snap)
    if screen:
        screen.hide_cat()
    if not a.lock:
        notify.notify("Would lock (actions.lock = false)", action.reason, a.notify)
        return
    if screen and cfg.cover.on_lock == "blur" and screen.cover_screen():
        log.warning("Screen covered: %s Type your keyword to uncover.", action.reason)
        return
    notify.notify("LOCK (dry run)" if a.dry_run else "Screen locked", action.reason, a.notify)
    lock.lock_screen(a.dry_run, a.command_timeout)


def cover_closed(cfg, rc, policy, now) -> None:
    """The cover went away: keyword typed (restart the cooldown) or killed (really lock)."""
    if rc == overlay.EXIT_UNLOCKED:
        log.info("Screen uncovered with the keyword.")
    else:
        log.warning("Screen cover closed without the keyword (exit %s): locking instead.", rc)
        lock.lock_screen(cfg.actions.dry_run, cfg.actions.command_timeout)
    policy.reset(now)
    policy.cooldown_until = now + cfg.timing.cooldown


def draw(frame, tracks, policy, now, cfg) -> None:
    for t in tracks:
        x, y, w, h = map(int, t.box)
        if t.is_owner:
            color, label = GREEN, "you"
        elif t.looking:
            color, label = RED, "WATCHING"
        else:
            color, label = ORANGE, "stranger"
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.putText(frame, f"{label} sim={t.sim:.2f} yaw={t.yaw:+.2f} pitch={t.pitch:.2f}",
                    (x, max(15, y - 8)), FONT, 0.45, color, 1)
    bar = f"watched {policy.watched:.1f}/{cfg.timing.lock_after:.0f}s"
    if policy.in_cooldown(now):
        bar += "  (cooldown)"
    if cfg.actions.dry_run:
        bar += "  DRY RUN"
    cv2.putText(frame, bar, (10, 25), FONT, 0.7, WHITE, 2)


def run(cfg, preview: bool = False, use_tray: bool = True) -> int:
    if preview and not gui_available():
        raise UserError("--preview needs OpenCV with GUI support "
                        "(pip install opencv-python, not opencv-python-headless).")
    detector, recognizer = load_engine(cfg)
    gallery = load_owner(cfg)
    enrolled = gallery is not None
    t = cfg.timing
    if not enrolled:
        log.warning("No enrolled face: FALLBACK mode, the biggest face is assumed to be you. "
                    "Run `shoulder-guard enroll` for real owner recognition.")
        if t.away_lock:
            log.warning("timing.away_lock needs enrollment; ignoring it.")

    i = cfg.identity
    tracker = Tracker(i.track_ttl, i.iou_match, i.owner_votes_window, i.look_votes_window)
    policy = Policy(t.warn_after, t.lock_after, t.grace, t.cooldown, t.away_lock)
    camera = Camera(cfg.camera, on_unavailable=lambda: notify.notify(
        "Camera unavailable", "Webcam busy or unplugged. Retrying in the background.",
        cfg.actions.notify))
    screen = overlay.ScreenOverlay(cfg.home)
    if cfg.cover.on_lock == "blur" and not overlay.load_keyword(cfg.home):
        log.warning("cover.on_lock = blur but no keyword is set: the OS lock will be used. "
                    "Run `shoulder-guard set-keyword`.")
    state = State()
    tray = None
    if use_tray:
        from shoulder_guard.tray import start_tray
        tray = start_tray(state, cfg)

    log.info("ShoulderGuard running (%s, warn %.1fs, lock %.1fs%s%s). Ctrl+C to stop.",
             "owner recognition" if enrolled else "fallback", t.warn_after, t.lock_after,
             f", away {t.away_lock:.0f}s" if t.away_lock and enrolled else "",
             ", DRY RUN" if cfg.actions.dry_run else "")
    period, frames, started, was_paused = 1.0 / t.fps, 0, time.monotonic(), False
    try:
        while not state.stop.is_set():
            t0 = time.monotonic()
            if screen.covered:
                rc = screen.cover_result()
                if rc is None:  # still covered: camera off until the keyword is typed
                    camera.release()
                    state.status = "covered"
                    state.stop.wait(period)
                    continue
                cover_closed(cfg, rc, policy, t0)
            if state.paused(t0):
                screen.hide_cat()
                if not was_paused:
                    log.info("Paused.")
                    camera.release()
                was_paused, state.status = True, "paused"
                state.stop.wait(period)
                continue
            if was_paused:
                log.info("Resumed.")
                policy.reset(t0)
                was_paused = False

            frame = camera.read()
            if frame is None:
                state.status = "idle" if camera.available else "camera"
                state.stop.wait(period)
                continue
            now = time.monotonic()
            frames += 1
            tracks = analyse(frame, now, detector.detect(frame), recognizer.embed, tracker,
                             gallery, cfg)
            for action in policy.step(now, tracks, enrolled):
                perform(cfg, action, frame, screen)
            if policy.episode_start is None:
                screen.hide_cat()
            state.status = "watching" if policy.episode_start is not None else "idle"

            if preview:
                draw(frame, tracks, policy, now, cfg)
                cv2.imshow(RUN_WINDOW, frame)
                if cv2.waitKey(1) & 0xFF == QUIT_KEY:
                    break
            state.stop.wait(max(0.0, period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.monotonic() - started
        camera.release()
        screen.hide_cat()  # a cover stays up on purpose: only the keyword removes it
        if preview:
            cv2.destroyAllWindows()
        if tray:
            tray.stop()
        log.info("Stopped: %d frames analysed in %.1fs (%.1f fps).", frames, elapsed,
                 frames / elapsed if elapsed else 0)
    return 0


# -------------------------------------------------------------------- enroll

def collect_samples(frames, detect, embed, ident, clock=time.monotonic, show=None) -> list:
    """Capture an embedding whenever exactly ONE face is visible, at least
    `enroll_interval` apart, until `enroll_samples` or `enroll_timeout`.
    `show(frame, faces, n)` returning False aborts (q pressed)."""
    feats, last, start = [], -math.inf, clock()
    for frame in frames:
        faces = detect(frame)
        now = clock()
        if len(faces) == 1 and now - last >= ident.enroll_interval:
            feats.append(embed(frame, faces[0]))
            last = now
        if show and show(frame, faces, len(feats)) is False:
            break
        if len(feats) >= ident.enroll_samples or now - start >= ident.enroll_timeout:
            break
    return feats


def enroll(cfg, append: bool = False) -> int:
    if not gui_available():
        raise UserError("enroll needs OpenCV with GUI support "
                        "(pip install opencv-python, not opencv-python-headless).")
    detector, recognizer = load_engine(cfg)
    c, ident = cfg.camera, cfg.identity
    cap = open_capture(c.index, c.width, c.height)
    if not cap.isOpened():
        raise UserError(f"Could not open webcam {c.index}. Is another app using it?")

    def frames():
        fails = 0
        while True:
            ok, frame = cap.read()
            if ok:
                fails = 0
                yield frame
            else:
                fails += 1
                if fails >= c.reconnect_after_failures:
                    raise UserError("The webcam stopped delivering frames.")

    def show(frame, faces, n):
        view = frame.copy()
        if len(faces) == 1:
            x, y, w, h = map(int, faces[0][:4])
            cv2.rectangle(view, (x, y), (x + w, y + h), GREEN, 2)
            msg = f"capturing {n}/{ident.enroll_samples}"
        else:
            msg = "need exactly ONE face in view" if faces else "no face found - more light?"
        cv2.putText(view, msg, (10, 30), FONT, 0.8, YELLOW, 2)
        cv2.imshow(ENROLL_WINDOW, view)
        return cv2.waitKey(1) & 0xFF != QUIT_KEY

    print("\nENROLLMENT\n  Sit normally, only YOU in view. Slowly move your head a little:\n"
          "  left, right, up, down. Press q to abort.\n", flush=True)
    try:
        feats = collect_samples(frames(), detector.detect, recognizer.embed, ident, show=show)
    finally:
        cap.release()
        cv2.destroyAllWindows()
    if len(feats) < ident.min_enroll_samples:
        raise UserError(f"Only {len(feats)} samples captured (need {ident.min_enroll_samples}). "
                        "Try again with better lighting.")
    path = cfg.home / "owner.npy"
    try:
        gallery = save_gallery(path, feats, append)
    except ValueError as e:
        raise UserError(str(e)) from e
    log.info("Saved %d face samples to %s (%d new).", len(gallery), path, len(feats))
    return 0


# -------------------------------------------------------------------- doctor

PASS, WARN_, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


def doctor(cfg, test_lock: bool = False, check_camera: bool = True) -> int:
    rows = [("config", PASS, str(cfg.config_path))]
    has_api = hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")
    rows.append(("opencv", PASS if has_api else FAIL,
                 cv2.__version__ + ("" if has_api else " (needs >= 4.8)")))
    rows.append(("gui (preview/enroll)", PASS if gui_available() else WARN_,
                 "ok" if gui_available() else "headless OpenCV: no --preview / enroll"))

    for key in models.MODELS:
        st = models.status(key, cfg.home / "models")
        rows.append((f"model {key}", {"ok": PASS, "missing": WARN_}.get(st, FAIL),
                     st + (" (downloaded on first run)" if st == "missing" else "")))
    try:
        g = load_gallery(cfg.home / "owner.npy")
        rows.append(("enrollment", PASS if g is not None else WARN_,
                     f"{len(g)} samples" if g is not None else "not enrolled: fallback mode"))
    except ValueError as e:
        rows.append(("enrollment", FAIL, str(e)))

    if check_camera:
        cap = open_capture(cfg.camera.index, cfg.camera.width, cfg.camera.height)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        rows.append(("camera", PASS if ok else FAIL,
                     f"index {cfg.camera.index}: {frame.shape[1]}x{frame.shape[0]}" if ok
                     else f"index {cfg.camera.index}: no frames (busy or missing?)"))
    else:
        rows.append(("camera", SKIP, "--no-camera"))

    methods = lock.available_methods(cfg.actions.command_timeout)
    rows.append(("lock", PASS if methods else FAIL,
                 ", ".join(methods) if methods else "no screen locker found"))
    ok_overlay, detail = overlay.available()
    rows.append(("cover overlay", PASS if ok_overlay else WARN_,
                 detail if ok_overlay else f"{detail}: OS lock will be used"))
    has_kw = overlay.load_keyword(cfg.home) is not None
    rows.append(("unblur keyword", PASS if has_kw or cfg.cover.on_lock == "os" else WARN_,
                 "set" if has_kw else "not set: run `shoulder-guard set-keyword`"))
    rows.append(("notifications", PASS if notify.available() else WARN_,
                 "ok" if notify.available() else "no notifier; console log only"))
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        rows.append(("tray", PASS, "pystray + Pillow"))
    except Exception:
        rows.append(("tray", WARN_, "optional: pip install 'shoulder-guard[tray]'"))

    if test_lock:
        print(f"Locking the screen for real in {TEST_LOCK_DELAY}s ...", flush=True)
        time.sleep(TEST_LOCK_DELAY)
        ok = lock.lock_screen(dry_run=False, timeout=cfg.actions.command_timeout)
        rows.append(("test lock", PASS if ok else FAIL, "locked" if ok else "see log"))

    width = max(len(r[0]) for r in rows)
    print(f"\n{'CHECK'.ljust(width)}  RESULT  DETAIL")
    for name, result, detail in rows:
        print(f"{name.ljust(width)}  {result.ljust(6)}  {detail}")
    return 1 if any(r[1] == FAIL for r in rows) else 0
