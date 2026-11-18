# Progress

## Phase 1: Scaffold + config ✅
- Built: `.venv` (opencv-python 4.14.0, numpy, tomli, pystray, Pillow, pytest, ruff); `pyproject.toml` with the `shoulder-guard` entry point; `config.py` (defaults ← `~/.shoulder_guard/config.toml`, created on first run ← `--set` overrides; type, range and cross-field checks with clear messages); `default_config.toml` with every key commented; `cli.py` with all 5 subcommands.
- Files: `pyproject.toml`, `shoulder_guard/{__init__,__main__,cli,config}.py`, `shoulder_guard/default_config.toml`, `tests/test_config.py`, `reference/shoulder_guard.py`, `DECISIONS.md`.
- Tests: pytest 11 passed; ruff clean. `shoulder-guard run --set timing.fps=0` → `Config error: timing.fps must be between 0.1 and 60, got 0` (exit 2).

## Phase 2: Vision core ✅
- Built: `models.py` (download to `.part`, rejects LFS pointers, SHA-256 checked against the LFS `oid`s, then renamed); YuNet `Detector` (input size reset when the frame size changes, `min_face_px` filter); SFace `Recognizer` and an N×128 gallery (normalised, append, shape check); eye-line-frame `head_pose`; IoU `Tracker` (per-tracker IDs, best-IoU-first greedy matching, TTL, strict-majority votes).
- Real download check: both models downloaded to `~/.shoulder_guard/models`, hashes match (`yunet` 232589 B, `sface` 38696353 B, same as the pointer sizes); both load in cv2 4.14.0.
- Files: `shoulder_guard/models.py`, `shoulder_guard/vision/{__init__,detector,recognizer,pose,tracker}.py`, `tests/test_vision.py`, `tests/test_models.py`.
- Tests: pytest 26 passed; ruff clean.

## Phase 3: Policy engine ✅
- Built: `policy.py`, a pure state machine. `step(now, tracks, owner_enrolled)` returns `WARN` / `LOCK` / `AWAY_LOCK` actions. Watched time = `last_onlooker - episode_start` (grace never counted); one WARN per episode; cooldown after any lock; away lock fires once and re-arms only when the owner is seen again. `reset(now)` is used after a pause.
- Scenario tests (fake clock, 10 fps): 5 s watcher → WARN@1.0, LOCK@5.0; 0.6 s glance → nothing; glance plus grace → nothing (regression test for the prototype bug); head turned away → nothing; 0.3 s flicker → episode continues; owner alone → nothing; away_lock=2 → exactly one AWAY_LOCK; away re-arms when the owner returns; away needs enrollment; cooldown suppresses until 20 s; episode starting at t=0.0.
- Files: `shoulder_guard/policy.py`, `tests/test_policy.py`.
- Tests: pytest 37 passed; ruff clean.

## Phase 4: Platform layer ✅
- Built: `camera.py` (CAP_DSHOW on Windows; after N failed reads it notifies once, releases, and retries every `retry_seconds` without blocking; logs "Camera is back"); `notify.py` (notify-send / osascript with the message passed as argv / plyer; always logs; never raises; winsound or terminal-bell beep); `lock.py` (LockWorkStation / pmset / approved Linux order with session-ID lookup and per-command timeouts; clear error if nothing works; `dry_run` logs "would lock" and runs nothing; `available_methods()` checks without locking).
- This machine (checked without locking): `GNOME D-Bus Lock`, `loginctl lock-session 4`, `xdg-screensaver lock` all available; notify-send available.
- Files: `shoulder_guard/platform/{__init__,camera,notify,lock}.py`, `tests/test_platform.py`.
- Tests: pytest 46 passed; ruff clean.

## Phase 5: Enroll + main loop + preview + doctor ✅
- Built: `app.py`
  - `run`: throttled to `fps`; detect → pose and embedding per face → tracker → policy → actions; fallback owner vote; snapshot only if `snapshot_on_lock`; pause and resume; logs a summary on exit.
  - `--preview`: green = you, red = WATCHING, orange = stranger; sim, yaw and pitch on each box; "watched X/Y s"; q quits. cv2 window calls only happen when preview is on, and `gui_available()` is checked first.
  - `enroll`: captures only when exactly 1 face is visible, ≥ `enroll_interval` apart; 90 s timeout; `--append`; at least `min_enroll_samples`.
  - `doctor`: pass/fail table; `--test-lock` really locks after 3 s; `--no-camera` skips the webcam.
- Smoke test (webcam, headless, `run --dry-run`, not enrolled, 15 s): **144 frames in 14.7 s (9.8 fps), 0 tracebacks, clean exit on SIGINT, no snapshots written**.
- Bug found and fixed: pystray's `run_detached()` (X11 backend) left a non-daemon thread that blocked exit. The tray now runs `icon.run()` on a daemon thread.
- `doctor --no-camera` here: everything PASS, except enrollment WARN (not enrolled) and camera SKIP.
- Files: `shoulder_guard/app.py`, `shoulder_guard/tray.py` (written early: `run` uses it), `tests/test_app.py`.
- Tests: pytest 53 passed; ruff clean.

## Phase 6: Quality of life ✅
- Built: `tray.py` (optional pystray + Pillow; colours: green = idle, red = watching, grey = paused, yellow = camera unavailable; menu: status, Pause 15 min (`tray.pause_minutes`), Pause until resumed, Resume, Open config, Quit; returns None and the app carries on when the deps or the display are missing). Rotating log at `<home>/shoulder_guard.log` plus console (`[logging]` in config; the console handler is skipped under pythonw). `autostart.py`: Linux `~/.config/autostart/shoulder-guard.desktop`, macOS `~/Library/LaunchAgents/com.shoulderguard.agent.plist` (plistlib), Windows Startup-folder `ShoulderGuard.vbs` running pythonw hidden.
- Safety: autostart was tested only against temp directories (`--dir`); the real `~/.config/autostart` is untouched.
- Files: `shoulder_guard/platform/autostart.py`, `shoulder_guard/tray.py`, `shoulder_guard/cli.py` (logging), `tests/test_autostart.py`, `tests/test_app.py`.
- Tests: pytest 60 passed; ruff clean.

## Phase 7: Docs + final check ✅
- `README.md`: what it does, privacy statement, install, enroll, a tuning guide (misidentifies you / far-away people / side glances / more), per-OS locking, tray, autostart, development, known limitations.
- Final: **pytest 60 passed**, **ruff check: all checks passed**.

---

# FINAL SUMMARY

## Features
- Webcam → YuNet faces → SFace "is this you" → head pose "is it pointed at the screen" → IoU tracker with majority votes → pure policy state machine.
- Stranger looking ≥ 1 s → notification + beep; ≥ 5 s → lock; 15 s cooldown; optional away lock.
- Enrollment with `--append`; fallback "biggest face is you" when not enrolled.
- `--preview` debug window; `--dry-run`; `doctor` with a pass/fail table and `--test-lock`.
- Camera reconnect (busy or unplugged); notifications on Linux/macOS/Windows; per-OS locking with the approved Linux order.
- Optional tray (status colour, pause 15 min / until resumed, resume, open config, quit).
- Rotating log; install/uninstall autostart for all 3 OSes.
- All tunables in `~/.shoulder_guard/config.toml`, validated, overridable with `--set section.key=value`.
- 100% local; models SHA-256-verified against the Git-LFS oids; snapshots opt-in.

## How to run
```bash
cd /home/sm/onlook
source .venv/bin/activate               # or call .venv/bin/shoulder-guard directly
shoulder-guard enroll                   # register YOUR face (saved to ~/.shoulder_guard/owner.npy)
shoulder-guard run --preview --dry-run  # watch it work, never locks
shoulder-guard run                      # real use
shoulder-guard doctor                   # health check
```

## Test summary
- 60 unit tests (config 11, vision 13, models 4, policy 11, platform 9, app 8, autostart 6), no webcam or models needed. ruff clean.
- Real model download: both hashes match the LFS pointer oids.
- Webcam smoke test (`run --dry-run`, 15 s, not enrolled): 144 frames, 9.8 fps, 0 exceptions, clean exit, no snapshots.
- Lock path: verified with dry_run plus mocked runners, and by checking that GNOME D-Bus `Lock()`, `loginctl lock-session 4` and `xdg-screensaver` exist. **The screen was never actually locked.**
- Autostart: tested in temp dirs only. **Nothing was installed on this machine.**

## Decisions
See `DECISIONS.md` (user-given decisions plus 22 build decisions, one line each).

## Known limitations
- Head direction, not eye gaze; no liveness check (photos count as faces).
- Fallback mode (not enrolled) fails when the onlooker is closer than you.
- Tray on GNOME/Wayland uses pystray's X11 backend (PyGObject would need apt packages, which were off-limits), so the icon may be invisible. The app works regardless.
- Tray on macOS may not appear (it wants the main thread).
- Windows/macOS code paths are unit-tested only, not run on real machines.
- Not run by me (they need you): enrollment, the `--preview` window, a real lock, real autostart.

## Manual checklist for you
1. **Enroll (register your face):** `.venv/bin/shoulder-guard enroll`. Sit alone in front of the camera and slowly move your head left, right, up and down until it reports `Saved 40 face samples`. Repeat later with `enroll --append` in different lighting or with glasses on.
2. **Preview + dry run:** `.venv/bin/shoulder-guard run --preview --dry-run`
   - [ ] Your box is green with `sim` well above 0.38
   - [ ] Turn your head away: you stay green; nothing triggers
   - [ ] A second person looks at the screen: red WATCHING box, notification and beep at ~1 s, "LOCK (dry run)" at ~5 s, then "(cooldown)" for 15 s
   - [ ] A second person with their head turned away: orange box, no trigger
   - [ ] A quick glance (< 1 s): nothing
   - [ ] Cover the camera or open it in another app: "Camera unavailable" once; "Camera is back" in the log afterwards
   - [ ] Tray (if visible): Pause 15 min turns it grey and frees the camera; Resume brings it back
   - [ ] `q` quits the window
3. **Real lock:** `.venv/bin/shoulder-guard doctor --test-lock`. It locks after 3 s; check the table says `test lock PASS`.
4. **Autostart:** `.venv/bin/shoulder-guard install-autostart`, log out and back in, then check `~/.shoulder_guard/shoulder_guard.log` shows "ShoulderGuard running". To undo: `.venv/bin/shoulder-guard uninstall-autostart`.

## Change 2026-11-17: cat badge + keyword blur cover ✅
- Notifications and beep off. WARN shows a cat "Someone is watching!" badge (top centre, never takes focus) and hides it when the episode ends. LOCK covers every monitor with a frosted full-screen overlay, removed only by the keyword + Enter; if the cover closes any other way, the OS lock runs.
- New: `shoulder_guard/overlay.py`, `shoulder-guard set-keyword`, a `[cover]` config section (`on_lock = "blur"|"os"`, `cat_on_warn`), doctor rows for the overlay and keyword, a "covered" tray state.
- Checked: GTK starts in the child (doctor PASS); both artworks rendered offscreen and inspected; the cat window shown live for 3 s and closed cleanly. The cover was **not** shown live (it needs the user's keyword).
- Tests: 69 passed (9 new in `tests/test_overlay.py`); ruff clean.
