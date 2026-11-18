# ShoulderGuard

ShoulderGuard watches your webcam for **someone other than you looking at your screen**.
After `warn_after` seconds (default 1 s) of watching you get a desktop notification and a beep.
If they keep looking for `lock_after` seconds (default 2 s), the PC locks.
It can also lock when *you* walk away (`away_lock`).

How it works:
- [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) finds faces and 5 landmarks.
- [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) decides whether each face is you.
- Head pose estimated from the landmarks decides whether the face points at the screen.
- A tracker with majority voting smooths out flicker.
- A small, fully tested state machine decides when to warn and when to lock.

## What happens when someone watches

1. **After 1 s:** the tray cat turns red. (Set `cover.cat_on_warn = true` for a small
   *"Someone is watching!"* badge at the bottom of the screen too.)
2. **After 2 s:** the PC locks (`cover.on_lock = "os"`, the default). Set `cover.on_lock = "blur"`
   for a frosted full-screen cover instead; type your keyword and press Enter to remove it.
   Backspace and Esc edit what you've typed. A wrong keyword pauses input for 1 s.
   - Set the keyword first: `shoulder-guard set-keyword`. Only a salted PBKDF2 hash is stored,
     in `~/.shoulder_guard/keyword` (mode 600).
   - If the cover is closed any other way (killed, crashed), the **real OS lock** kicks in.
   - With no keyword set, or no GTK available, the OS lock is used instead.
   - `cover.on_lock = "os"` always uses the OS lock screen.

Desktop notifications and the beep are off by default (`actions.notify`, `actions.beep`).

## Privacy

- **Everything runs locally.** No frame, embedding or log ever leaves the machine.
- The only network access is the one-time model download from GitHub (opencv_zoo).
  Each file is checked against a pinned SHA-256 before use.
- Your face is stored as 128-number embeddings in `~/.shoulder_guard/owner.npy`, not as photos.
- **Snapshots are off by default.** With `actions.snapshot_on_lock = true`, a photo of the
  onlooker is saved to `~/.shoulder_guard/snapshots/` when the PC locks.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[tray]"        # tray is optional; drop [tray] to skip it
.venv/bin/shoulder-guard doctor           # checks camera, models, lock, notifications
```

Requires Python 3.9 or newer and `opencv-python>=4.8,<5`, not the `-headless` build: preview and
enroll need a GUI. The models (~39 MB) download to `~/.shoulder_guard/models` on first use.

## Use

```bash
shoulder-guard enroll                      # once: teach it your face (~10 s, only you in view)
shoulder-guard enroll --append             # add samples: other lighting, glasses, headset
shoulder-guard run --preview --dry-run     # test: debug window, never locks
shoulder-guard run                         # for real (tray icon if available)
shoulder-guard doctor --test-lock          # REALLY lock once, to prove locking works
```

Preview colours: **green** = you, **red** = WATCHING, **orange** = stranger not looking.
Each box shows `sim` (similarity to you), `yaw` and `pitch`. The corner shows `watched X/Y s`.

Without enrollment, ShoulderGuard runs in *fallback mode*: the biggest (closest) face is assumed
to be you. This is much weaker, so enroll.

## Configuration

`~/.shoulder_guard/config.toml` is created from [default_config.toml](shoulder_guard/default_config.toml)
on first run. Every key there is commented. Any key can be overridden for one run:

```bash
shoulder-guard run --set timing.lock_after=8 --set detection.min_face_px=60
```

Bad values stop the program with a clear message, for example `timing.fps must be between 0.1 and 60`.

### Tuning guide

Run `shoulder-guard run --preview --dry-run` and read the numbers on the boxes.

| Problem | Change |
|---|---|
| **You're flagged as a stranger** (your box is orange or red) | Look at your `sim`. Re-enroll with `--append` in the problem lighting or with glasses on. Otherwise lower `identity.match_threshold` (default 0.38) to just below your typical `sim`, e.g. 0.32. |
| **A stranger is recognised as you** | Raise `identity.match_threshold` (e.g. 0.45). Re-enroll with only you in view. |
| **People far away trigger it** | Raise `detection.min_face_px` (default 32) above their box size, e.g. 60–80. |
| **Side glances trigger it** | Lower `detection.max_yaw` (default 0.6 ≈ 30°), e.g. 0.4. Raise `timing.warn_after` / `timing.lock_after`. |
| **Someone looking down at a laptop on their lap triggers it** | Lower `detection.pitch_max` (default 1.0). |
| **Locks too slowly or too quickly** | `timing.warn_after`, `timing.lock_after`, `timing.cooldown`. |
| **Brief detection dropouts restart the timer** | Raise `timing.grace` (default 0.8 s). Grace time is never counted as watching. |
| **Objects (posters, photos) detected as faces** | Raise `detection.score_threshold` (default 0.75). |
| **High CPU** | Lower `timing.fps` (default 10), e.g. 5. |

### Locking per OS

- **Windows**: `LockWorkStation`.
- **macOS**: `pmset displaysleepnow` only turns the display off. Turn on *System Settings → Lock
  Screen → Require password immediately after screen saver begins or display is turned off*,
  or the "lock" won't need a password.
- **Linux**: tried in order: GNOME D-Bus `org.gnome.ScreenSaver.Lock`, `loginctl lock-session <id>`
  (the session ID is looked up if `XDG_SESSION_ID` is unset), `xdg-screensaver lock`,
  `gnome-screensaver-command -l`, `dm-tool lock`. `shoulder-guard doctor` shows which ones exist.

## Tray

With `pystray` and `Pillow` installed, a coloured cat shows the status: green = on guard,
red = someone watching, grey = paused, orange = camera unavailable. The menu has Pause 15 min,
Pause until resumed, Resume, Open config and Quit. Pausing releases the webcam, so you can join a call.
Use `run --no-tray` to disable it.

## Autostart

```bash
shoulder-guard install-autostart     # Linux: ~/.config/autostart/shoulder-guard.desktop
                                     # macOS: ~/Library/LaunchAgents/com.shoulderguard.agent.plist
                                     # Windows: Startup folder ShoulderGuard.vbs (pythonw, no console)
shoulder-guard uninstall-autostart
```

The entry runs the exact Python that installed it (your venv), so the venv doesn't need activating.
Logs go to `~/.shoulder_guard/shoulder_guard.log` (rotating).

## Development

```bash
.venv/bin/pip install -e ".[dev,tray]"
.venv/bin/pytest          # no webcam or models needed
.venv/bin/ruff check .
```

Layout: `vision/` (detector, recognizer, pose, tracker) and `policy.py` are pure and unit-tested.
`platform/` holds everything OS-specific. `app.py` wires them together. `reference/shoulder_guard.py`
is the original single-file prototype, kept for comparison.

## Known limitations

- **The blur cover is a privacy curtain, not a security lock.** On GNOME/Wayland no app can grab
  the whole keyboard, so someone who knows Linux can get past it with Alt+Tab, the Super key or
  Ctrl+Alt+F3. Use `cover.on_lock = "os"` when you need a real lock. The cover can't show your
  actual screen blurred, because Wayland doesn't let apps capture it silently. It shows a frosted
  look instead, which also hides more.
- The cat and the cover need GTK 3 (`python3-gi`, present on Ubuntu). The venv borrows it from
  `/usr/lib/python3/dist-packages`. Without it, the OS lock is used.

- **Head direction, not eye gaze.** Someone with their head turned away but their eyes on your screen
  isn't detected. Someone facing the camera but reading something next to it is.
- **No liveness check.** A photo or video of a face counts as a face, and a photo of *you* counts as you.
- **Fallback mode** (not enrolled) assumes the closest face is you. It fails when the onlooker is closer.
- **Tray on GNOME/Wayland**: pystray has no AppIndicator backend in a plain venv (PyGObject needs
  system packages), so it falls back to an X11 tray icon. It may not show unless the Ubuntu AppIndicator
  extension's legacy-tray support is on. Everything else works without the icon.
- **Tray on macOS** wants the main thread. ShoulderGuard runs it on a background thread, so it may not
  appear there.
- **Windows and macOS** paths are unit-tested but haven't been run on real machines yet.
- **Lighting**: backlit faces or very dark rooms reduce detection. Use `enroll --append` for the
  conditions you work in.
- **Camera sharing**: on Windows a webcam in use by Teams or Meet is usually exclusive.
  ShoulderGuard reports "camera unavailable" and retries every `camera.retry_seconds`.
