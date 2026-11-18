# Decisions

One line each: what was chosen and why.

## Given by the user
- opencv-python pinned `>=4.8,<5` (4.14.0 installed in `.venv`): 5.0 is too new to trust for YuNet/SFace.
- Linux lock order: GNOME D-Bus `org.gnome.ScreenSaver.Lock` → `loginctl lock-session <id>` (ID looked up when `XDG_SESSION_ID` is empty) → `xdg-screensaver lock` → `gnome-screensaver-command -l` → `dm-tool lock`.
- Model SHA-256s come from the Git-LFS pointer files on raw.githubusercontent.com, not from a download checking itself.

## Made during the build
- The prototype is saved as `reference/shoulder_guard.py` (excluded from ruff): it is the behaviour reference I promised to keep.
- Everything is installed in `.venv` with `pip install -e ".[dev,tray]"`: the user asked for a venv; editable install gives the `shoulder-guard` command.
- `tomli` is used on Python < 3.11, `tomllib` otherwise: this machine has 3.10.
- Extra config keys (`detection.nms_threshold`, `detection.top_k`, `identity.enroll_interval`, `identity.enroll_timeout`, `identity.min_enroll_samples`, `camera.retry_seconds`, `actions.command_timeout`, `[logging]`, `[tray]`): these replace the prototype's hard-coded numbers.
- Unknown config keys and sections are errors: a typo like `lock_aftr` would otherwise be silently ignored.
- CLI overrides use a generic `--set section.key=value` (TOML syntax), with `--dry-run` as a shortcut: one mechanism covers every key with no per-key flags to keep in sync.
- `timing.fps` is a float: allows e.g. 2.5 fps on slow machines.
- `doctor --no-camera` added: lets doctor run while another app holds the webcam, and let me test it without using the webcam.
- Tracker matches the highest-IoU pairs first, not boxes in order: in the prototype an earlier box could take the track that fits a later box better.
- Track IDs are counted per tracker, not on the class: test runs can't affect each other.
- The "biggest face is owner" fallback vote is computed in `app.py` with `tracker.biggest_index`, not inside `policy.py`: votes are stored on tracks, and policy has to stay pure (no changes to its inputs).
- Gallery is re-normalised on load: guards against an `owner.npy` written by another tool or by the prototype.
- Policy times come from `time.monotonic()` in the app: wall-clock jumps (NTP, suspend/resume) can't fake or erase watching time.
- The camera never sleeps inside `read()` while reconnecting (the prototype slept 3 s): the tray and preview stay responsive while the webcam is busy.
- macOS notifications pass title and message as `osascript` argv: a quote in the message can't break or inject AppleScript.
- `lock.lock_screen` takes the command runner as a parameter: the lock order is unit-tested without ever locking.
- `enroll` captures through a pure `collect_samples()` with an injected clock and frame source: the capture rules are unit-tested without a camera.
- `doctor` reports missing models as WARN, not FAIL: `run` downloads them on first use.
- The tray runs `icon.run()` on a daemon thread instead of `run_detached()`: the X11 backend's thread otherwise kept the process alive after Ctrl+C.
- Pausing releases the webcam: you can join a call while paused; the policy resets on resume so no stale episode or away timer fires.
- Windows autostart is a `.vbs` in the Startup folder that runs pythonw hidden: a `.lnk` needs COM (pywin32) and Task Scheduler "at logon" often needs admin; a `.cmd` would flash a console.
- `install-autostart --dir` / `uninstall-autostart --dir` added: lets the entry be tested in a temp folder without touching the real login items.
- Autostart runs `<venv python> -m shoulder_guard run`: points at the exact interpreter, so it works without activating the venv.
- Ruff is used as a linter only (`ruff check`, all clean); `ruff format` wasn't applied, since the spec asked for a lint pass and reformatting 19 files would only reshuffle style.
- pystray's logger is set to CRITICAL: its X11 backend prints 'Failed to dock icon' tracebacks whenever the GNOME lock screen removes the tray host; the icon is cosmetic.
- Tray re-docks after a GNOME lock by redrawing while pystray's X11 backend has no tray host: pystray only retries docking on redraw; AppIndicator would fix it properly but needs apt packages.

## Cat badge + blur cover (user request, 2026-11-17)
- Notifications and the beep are off by default and in the user's config: the user asked for no notifications, only the tray dot.
- The overlay runs in a child process (`python -m shoulder_guard.overlay`), not a thread: the GTK main loop stays separate from the camera loop and from OpenCV's own Qt GUI, and a crash can't take the guard down.
- GTK 3 comes from the system's `python3-gi` (path added in the child only): tkinter isn't installed, and PyGObject in the venv would need apt build deps; both options were off-limits.
- Windows use `GDK_BACKEND=x11` (XWayland): on Wayland, apps can't keep a window on top or position it.
- The cover is a frosted gradient, not a blurred screenshot: GNOME/Wayland blocks silent screen capture, and an opaque cover hides more anyway.
- Only `exit 0` (correct keyword) uncovers quietly; any other exit (killed, crashed, closed) falls back to the real OS lock, so killing the cover isn't a bypass.
- The keyword is stored as a salted PBKDF2-SHA256 hash (200k iterations) in `<home>/keyword`, mode 600, set with `shoulder-guard set-keyword` (hidden input, typed twice): never in plain text, not in the config or chat.
- A wrong keyword ignores input for 1 s: slows down someone guessing at the keyboard.
- The camera is released while the cover is up, and cooldown restarts when it is removed: no re-trigger while you type the keyword.
- `dry_run` still shows the cat and the cover (both reversible); it only suppresses the real OS lock.
- When the app quits, the cover stays up: only the keyword should remove it.
- The cover unlocks the moment the last typed characters match (no Enter needed): user request. The keyword length is stored in the hash so only one PBKDF2 check runs per keystroke; it reveals only the length. Enter still works, and hashes saved without a length still need Enter.
