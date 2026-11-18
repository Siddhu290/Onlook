#!/usr/bin/env python3
"""
ShoulderGuard v2 - know when someone is reading your screen.

Pipeline (100% local, no frame ever leaves your machine):
  webcam
    -> YuNet face detector (deep-learning, + 5 facial landmarks)
    -> SFace face recognition ............ is this face YOU?
    -> head pose from landmarks .......... is this face pointed at the screen?
    -> tracker + vote smoothing .......... ignores one-frame glitches
    -> stranger looking for --warn sec  => desktop notification + beep
       stranger looking for --lock sec  => lock the PC
       (optional) you gone for --away sec => lock the PC

Quick start:
  pip install "opencv-python>=4.8" numpy plyer
  python shoulder_guard.py --enroll              # once: teach it your face (~10 s)
  python shoulder_guard.py --preview --dry-run   # test: debug window, never locks
  python shoulder_guard.py                       # run for real

Models (~40 MB total) are downloaded on first run to ~/.shoulder_guard/models
"""
import argparse
import collections
import os
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

SYSTEM = platform.system()
HOME = Path(os.environ.get("SHOULDER_GUARD_HOME", Path.home() / ".shoulder_guard"))
OWNER_FILE = HOME / "owner.npy"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}


# ======================================================================= models
def model_path(key: str) -> str:
    name, url = MODELS[key]
    path = HOME / "models" / name
    if path.exists() and path.stat().st_size > 100_000:
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    print(f"Downloading {name} ...", flush=True)
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as e:
        raise SystemExit(f"Download failed ({e}).\nDownload it manually from:\n  {url}\n"
                         f"and save it as:\n  {path}")
    if tmp.stat().st_size < 100_000:  # got a Git-LFS pointer or an error page
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Downloaded file looks wrong. Get it manually from:\n  {url}\n"
                         f"and save it as:\n  {path}")
    tmp.replace(path)
    return str(path)


class FaceEngine:
    def __init__(self, score_threshold: float = 0.75):
        self.det = cv2.FaceDetectorYN.create(model_path("yunet"), "", (320, 320),
                                             score_threshold, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(model_path("sface"), "")
        self._size = None

    def detect(self, frame):
        """Returns list of rows: [x, y, w, h, 10 landmark coords, score]."""
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self.det.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self.det.detect(frame)
        return [] if faces is None else list(faces)

    def embed(self, frame, face) -> np.ndarray:
        feat = self.rec.feature(self.rec.alignCrop(frame, face)).flatten()
        return feat / (np.linalg.norm(feat) + 1e-9)


# ==================================================================== head pose
def head_pose(face):
    """
    Rough yaw/pitch from YuNet's 5 landmarks (no 3D model needed), measured in
    the eye-line's own frame so a tilted head (roll) doesn't fool it.
      yaw   : 0 when facing the camera; ~0.6 at ~30 deg turn; grows with the turn
      pitch : ~0.5 when level, smaller = looking up, bigger = looking down
    The webcam sits on the screen, so 'facing the camera' ~= 'facing the screen'.
    """
    re, le, nose, rm, lm = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
    eye_mid, mouth_mid = (re + le) / 2, (rm + lm) / 2
    v = le - re
    half = np.linalg.norm(v) / 2 + 1e-9
    u = v / (2 * half)                 # unit vector along the eyes
    perp = np.array([-u[1], u[0]])     # unit vector pointing down the face
    n = nose - eye_mid
    yaw = float(np.dot(n, u) / half)
    pitch = float(np.dot(n, perp) / (np.dot(mouth_mid - eye_mid, perp) + 1e-9))
    return yaw, pitch


# ====================================================================== tracker
def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class Track:
    _next_id = 0

    def __init__(self, box, now):
        self.id = Track._next_id
        Track._next_id += 1
        self.box, self.last = box, now
        self.owner_votes = collections.deque(maxlen=7)  # smooths recognition
        self.look_votes = collections.deque(maxlen=5)   # smooths head pose
        self.sim = self.yaw = self.pitch = 0.0

    @staticmethod
    def _majority(votes):
        return bool(votes) and sum(votes) * 2 > len(votes)

    @property
    def is_owner(self):
        return self._majority(self.owner_votes)

    @property
    def looking(self):
        return self._majority(self.look_votes)


class Tracker:
    def __init__(self, ttl: float = 1.0):
        self.tracks, self.ttl = [], ttl

    def update(self, boxes, now):
        """Match boxes to existing tracks by IoU. Returns tracks aligned with boxes."""
        free, out = list(self.tracks), []
        for b in boxes:
            best = max(free, key=lambda t: iou(t.box, b), default=None)
            if best is not None and iou(best.box, b) > 0.25:
                free.remove(best)
                best.box, best.last = b, now
                out.append(best)
            else:
                t = Track(b, now)
                self.tracks.append(t)
                out.append(t)
        self.tracks = [t for t in self.tracks if now - t.last <= self.ttl]
        return out


# =================================================================== OS helpers
_plyer = None
try:
    from plyer import notification as _plyer
except Exception:
    pass


def notify(title: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {title} - {msg}", flush=True)
    try:
        if SYSTEM == "Linux":
            subprocess.Popen(["notify-send", "-u", "critical", "-a", "ShoulderGuard", title, msg])
            return
        if SYSTEM == "Darwin":
            subprocess.Popen(["osascript", "-e", f'display notification "{msg}" with title "{title}"'])
            return
        if _plyer:
            _plyer.notify(title=title, message=msg, app_name="ShoulderGuard", timeout=4)
    except Exception:
        pass  # console line above is the fallback


def beep() -> None:
    try:
        if SYSTEM == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def lock_screen() -> None:
    if SYSTEM == "Windows":
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return
    if SYSTEM == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"])  # needs "require password immediately"
        return
    for cmd in (["loginctl", "lock-session"],
                ["xdg-screensaver", "lock"],
                ["gnome-screensaver-command", "-l"],
                ["dm-tool", "lock"]):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0:
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    print("Could not lock the screen: no supported screen locker found.")


def open_camera(index: int):
    backend = cv2.CAP_DSHOW if SYSTEM == "Windows" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


# =================================================================== enrollment
def enroll(engine: FaceEngine, args) -> None:
    cap = open_camera(args.camera)
    if not cap.isOpened():
        raise SystemExit("Could not open the webcam.")
    print("\nENROLLMENT\n  Sit normally, only YOU in view. Slowly move your head a little:\n"
          "  left, right, up, down. Press q to abort.\n")
    feats, last, target = [], 0.0, args.samples
    start = time.time()
    while len(feats) < target and time.time() - start < 90:
        ok, frame = cap.read()
        if not ok:
            continue
        faces = engine.detect(frame)
        view = frame.copy()
        if len(faces) == 1:
            x, y, w, h = map(int, faces[0][:4])
            cv2.rectangle(view, (x, y), (x + w, y + h), (0, 200, 0), 2)
            if time.time() - last > 0.2:
                feats.append(engine.embed(frame, faces[0]))
                last = time.time()
            msg = f"capturing {len(feats)}/{target}"
        else:
            msg = "need exactly ONE face in view" if faces else "no face found - more light?"
        cv2.putText(view, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow("ShoulderGuard - enrollment", view)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if len(feats) < 10:
        raise SystemExit("Not enough samples captured. Try again with better lighting.")
    gallery = np.stack(feats)
    if args.append and OWNER_FILE.exists():
        gallery = np.concatenate([np.load(OWNER_FILE), gallery])
    HOME.mkdir(parents=True, exist_ok=True)
    np.save(OWNER_FILE, gallery)
    print(f"Saved {len(gallery)} face samples to {OWNER_FILE}")


# ========================================================================= guard
def guard(engine: FaceEngine, args) -> None:
    gallery = np.load(OWNER_FILE) if OWNER_FILE.exists() else None
    if gallery is None:
        print("No enrolled face found -> fallback mode: the biggest face is assumed to be you.\n"
              "Run with --enroll for much better accuracy.")
        if args.away:
            print("--away needs enrollment, ignoring it.")
            args.away = 0

    tracker = Tracker()
    cap = open_camera(args.camera)
    fails = 0
    episode_start, last_onlooker, warned = None, 0.0, False
    cooldown_until = 0.0
    owner_last_seen, away_armed = time.time(), True
    period = 1.0 / max(1, args.fps)
    snap_dir = HOME / "snapshots"

    def act(reason: str, frame):
        nonlocal cooldown_until
        if args.snapshot and frame is not None:
            snap_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(snap_dir / time.strftime("%Y%m%d-%H%M%S.jpg")), frame)
        if args.dry_run:
            notify("LOCK (dry run)", reason)
        else:
            notify("Screen locked", reason)
            lock_screen()
        cooldown_until = time.time() + args.cooldown

    mode = "owner recognition" if gallery is not None else "fallback"
    print(f"ShoulderGuard running ({mode}, warn {args.warn}s, lock {args.lock}s"
          f"{', away ' + str(args.away) + 's' if args.away else ''}"
          f"{', DRY RUN' if args.dry_run else ''}). Ctrl+C to stop.", flush=True)

    try:
        while True:
            t0 = time.time()
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            if not ok:
                fails += 1
                if fails == 30:
                    notify("Camera unavailable", "Webcam busy or unplugged. Retrying in the background.")
                if fails % 30 == 0:
                    cap.release()
                    time.sleep(3)
                    cap = open_camera(args.camera)
                time.sleep(0.1)
                continue
            if fails >= 30:
                print("Camera is back.", flush=True)
            fails = 0
            now = time.time()

            # ---------------------------------------------------------- vision
            faces = [f for f in engine.detect(frame) if min(f[2], f[3]) >= args.min_face]
            tracks = tracker.update([tuple(map(float, f[:4])) for f in faces], now)
            biggest = max(range(len(faces)), key=lambda i: faces[i][2] * faces[i][3], default=-1)
            for i, (f, t) in enumerate(zip(faces, tracks)):
                t.yaw, t.pitch = head_pose(f)
                t.look_votes.append(abs(t.yaw) <= args.max_yaw and 0.15 <= t.pitch <= 1.0)
                if gallery is not None:
                    t.sim = float(np.max(gallery @ engine.embed(frame, f)))
                    t.owner_votes.append(t.sim >= args.match)
                else:
                    t.owner_votes.append(i == biggest)

            onlookers = [t for t in tracks if not t.is_owner and t.looking]
            if any(t.is_owner for t in tracks):
                owner_last_seen, away_armed = now, True

            # ----------------------------------------------------- decisions
            if now < cooldown_until:
                episode_start, warned = None, False
            elif onlookers:
                episode_start = episode_start or now
                last_onlooker = now
            elif episode_start and now - last_onlooker > args.grace:
                episode_start, warned = None, False

            # count only up to the last frame someone was actually seen looking,
            # so the grace window never adds 'fake' watching time
            watched = last_onlooker - episode_start if episode_start else 0.0
            if episode_start and not warned and watched >= args.warn:
                notify("Someone is looking at your screen",
                       f"Locking in {max(0.0, args.lock - watched):.0f}s if they keep looking.")
                beep()
                warned = True
            if episode_start and watched >= args.lock:
                act(f"Someone watched your screen for {args.lock:.0f}s.", frame)
                episode_start, warned = None, False

            if args.away and away_armed and now >= cooldown_until \
                    and now - owner_last_seen >= args.away:
                act(f"You were away for {args.away:.0f}s.", None)
                away_armed = False  # re-armed when you're back

            # ------------------------------------------------------- preview
            if args.preview:
                for t in tracks:
                    x, y, w, h = map(int, t.box)
                    if t.is_owner:
                        color, label = (0, 200, 0), "you"
                    elif t.looking:
                        color, label = (0, 0, 255), "WATCHING"
                    else:
                        color, label = (0, 165, 255), "stranger"
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    cv2.putText(frame, f"{label} sim={t.sim:.2f} yaw={t.yaw:+.2f} pitch={t.pitch:.2f}",
                                (x, max(15, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                bar = f"watched {watched:.1f}/{args.lock:.0f}s"
                if now < cooldown_until:
                    bar += "  (cooldown)"
                cv2.putText(frame, bar, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.imshow("ShoulderGuard (q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            time.sleep(max(0.0, period - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()


# ========================================================================== CLI
def main() -> None:
    ap = argparse.ArgumentParser(description="Lock your PC when someone reads your screen.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--enroll", action="store_true", help="capture your face and exit")
    ap.add_argument("--append", action="store_true", help="with --enroll: add to existing samples "
                    "(e.g. enroll again in different lighting / with glasses)")
    ap.add_argument("--samples", type=int, default=40, help="face samples to capture when enrolling")
    ap.add_argument("--warn", type=float, default=1.0, help="seconds of watching before a notification")
    ap.add_argument("--lock", type=float, default=5.0, help="seconds of watching before locking")
    ap.add_argument("--away", type=float, default=0, help="lock if YOU are gone this long (0 = off)")
    ap.add_argument("--grace", type=float, default=0.8, help="detection gap tolerated before the timer resets")
    ap.add_argument("--cooldown", type=float, default=15, help="pause after a lock")
    ap.add_argument("--match", type=float, default=0.38, help="face-match threshold (higher = stricter)")
    ap.add_argument("--max-yaw", type=float, default=0.6, help="how far a head may turn and still count as looking")
    ap.add_argument("--min-face", type=int, default=32, help="ignore faces smaller than this (px), i.e. far away")
    ap.add_argument("--score", type=float, default=0.75, help="face detector confidence")
    ap.add_argument("--fps", type=int, default=10, help="frames analysed per second")
    ap.add_argument("--camera", type=int, default=0, help="webcam index")
    ap.add_argument("--snapshot", action="store_true", help="save a photo to ~/.shoulder_guard/snapshots on lock")
    ap.add_argument("--preview", action="store_true", help="show a debug window")
    ap.add_argument("--dry-run", action="store_true", help="never actually lock")
    args = ap.parse_args()

    engine = FaceEngine(args.score)
    enroll(engine, args) if args.enroll else guard(engine, args)


if __name__ == "__main__":
    main()
