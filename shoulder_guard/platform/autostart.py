"""Start ShoulderGuard at login: Linux .desktop, macOS LaunchAgent, Windows Startup folder."""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.shoulderguard.agent"
ENTRY_NAMES = {"linux": "shoulder-guard.desktop", "darwin": f"{LABEL}.plist",
               "win32": "ShoulderGuard.vbs"}


def _platform(platform: str) -> str:
    return "linux" if platform.startswith("linux") else platform


def default_dir(platform: str = sys.platform) -> Path:
    home = Path.home()
    if platform == "darwin":
        return home / "Library" / "LaunchAgents"
    if platform == "win32":
        return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "autostart"


def python_for_gui(platform: str = sys.platform, exe: str = sys.executable) -> str:
    """On Windows use pythonw.exe so no console window appears."""
    if platform == "win32":
        w = Path(exe).with_name("pythonw.exe")
        return str(w) if w.exists() else exe
    return exe


def entry(platform: str, python: str) -> tuple[str, bytes]:
    p = _platform(platform)
    args = [python, "-m", "shoulder_guard", "run"]
    if p == "darwin":
        data = plistlib.dumps({"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
                               "ProcessType": "Interactive"})
    elif p == "win32":
        # VBScript runs pythonw hidden; "" escapes a quote inside a VBS string
        data = f'CreateObject("WScript.Shell").Run """{python}"" -m shoulder_guard run", 0\r\n' \
            .encode()
    else:
        data = ("[Desktop Entry]\nType=Application\nName=ShoulderGuard\n"
                "Comment=Lock the screen when someone else reads it\n"
                f'Exec="{python}" -m shoulder_guard run\n'
                "X-GNOME-Autostart-enabled=true\nNoDisplay=false\n").encode()
    return ENTRY_NAMES[p], data


def install(target_dir=None, platform: str = sys.platform, python: str | None = None) -> Path:
    name, data = entry(platform, python or python_for_gui(platform))
    d = Path(target_dir) if target_dir else default_dir(platform)
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_bytes(data)
    return path


def uninstall(target_dir=None, platform: str = sys.platform) -> Path | None:
    d = Path(target_dir) if target_dir else default_dir(platform)
    path = d / ENTRY_NAMES[_platform(platform)]
    if path.exists():
        path.unlink()
        return path
    return None
