"""Tests for the Windows logon autostart launcher (work plan P1-b).

The real ``install``/``uninstall`` touch the user's Startup folder and are
exercised live on the machine; what needs regression protection is the
launcher's content and location — the wrong path means no autostart, the
wrong interpreter means a console window at every logon.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentd import autostart


def test_launcher_lives_in_the_user_startup_folder():
    path = autostart.launcher_path()
    assert path.name == autostart.LAUNCHER_NAME
    assert "Startup" in str(path)
    assert autostart.startup_folder() in path.parents


def test_launcher_runs_the_daemon_hidden():
    script = autostart.build_launcher()
    # hidden window (0) and no waiting for it (False)
    assert '", 0, False' in script
    # the interpreter path is quoted, inside the Run string, and closed:
    # """C:\...\pythonw.exe"" -m agentd.cli run
    assert '"" -m agentd.cli run"' in script
    assert script.count('"""') == 1


def test_launcher_prefers_pythonw_when_it_exists():
    script = autostart.build_launcher()
    interpreter = Path(sys.executable).with_name("pythonw.exe")
    if interpreter.exists():
        assert str(interpreter) in script
    else:
        assert sys.executable in script


def test_install_and_uninstall_round_trip(monkeypatch, tmp_path):
    """Point the launcher at a temp 'Startup' folder and round-trip it."""
    fake_startup = tmp_path / "Startup"
    monkeypatch.setattr(autostart, "startup_folder", lambda: fake_startup)
    assert not autostart.installed()

    assert "at logon" in autostart.install()
    assert autostart.installed()
    written = fake_startup / autostart.LAUNCHER_NAME
    assert "agentd.cli run" in written.read_text(encoding="utf-8")

    assert "removed" in autostart.uninstall()
    assert not autostart.installed()

    # uninstalling when nothing is installed stays calm
    assert "removed" in autostart.uninstall()
