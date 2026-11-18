import plistlib

import pytest

from shoulder_guard.cli import main
from shoulder_guard.platform import autostart

PY = "/opt/my env/bin/python"


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_install_uninstall_roundtrip(tmp_path, platform):
    path = autostart.install(tmp_path, platform=platform, python=PY)
    assert path.parent == tmp_path and path.exists()
    text = path.read_bytes()
    assert PY.encode() in text and b"shoulder_guard" in text
    assert autostart.uninstall(tmp_path, platform=platform) == path
    assert not path.exists()
    assert autostart.uninstall(tmp_path, platform=platform) is None


def test_mac_plist_valid(tmp_path):
    p = autostart.install(tmp_path, platform="darwin", python=PY)
    data = plistlib.loads(p.read_bytes())
    assert data["ProgramArguments"] == [PY, "-m", "shoulder_guard", "run"] and data["RunAtLoad"]


def test_linux_desktop_quotes_path_with_spaces(tmp_path):
    p = autostart.install(tmp_path, platform="linux", python=PY)
    assert f'Exec="{PY}" -m shoulder_guard run' in p.read_text()


def test_cli_install_into_temp_dir(tmp_path, capsys):
    cfg = ["--config", str(tmp_path / "c.toml"), "--set", f"paths.home='{tmp_path}'"]
    assert main(["install-autostart", "--dir", str(tmp_path / "auto"), *cfg]) == 0
    assert len(list((tmp_path / "auto").iterdir())) == 1
    assert main(["uninstall-autostart", "--dir", str(tmp_path / "auto"), *cfg]) == 0
    assert list((tmp_path / "auto").iterdir()) == []
    assert "Removed" in capsys.readouterr().out
