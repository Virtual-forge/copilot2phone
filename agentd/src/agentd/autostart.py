"""Windows logon autostart for the daemon (work plan P1-b).

``agentd install`` drops a tiny launcher into the *user's* Startup folder
(``%APPDATA%\\...\\Startup``) — the one autostart surface that needs no
admin rights (a logon scheduled task via ``schtasks /SC ONLOGON`` is
denied for standard users). The launcher runs the daemon through
``pythonw`` hidden, so nothing flashes at every logon.

The pure builders exist so the exact script content and paths can be
tested without touching the real Startup folder; ``install``/``uninstall``/
``installed`` are the only parts that touch the filesystem.
"""

from __future__ import annotations

import sys
from pathlib import Path

LAUNCHER_NAME = "agentd-daemon.vbs"

#: The daemon needs no console: it writes its own log file (paths.py), and a
#: flashing window at every logon would be obnoxious.
_WINDOWLESS = "pythonw.exe" if sys.platform == "win32" else "python"


def python_for_task() -> str:
    """The interpreter the launcher should run: pythonw when it exists."""
    windowless = Path(sys.executable).with_name(_WINDOWLESS)
    if windowless.exists():
        return str(windowless)
    return str(sys.executable)


def startup_folder() -> Path:
    """The per-user Startup folder (no admin rights required)."""
    import os

    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return (
        base
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def launcher_path() -> Path:
    return startup_folder() / LAUNCHER_NAME


def build_launcher() -> str:
    """The .vbs that starts the daemon hidden at logon."""
    # VBS: CreateObject("WScript.Shell").Run """<pythonw>"" -m agentd.cli run", 0, False
    line = (
        'CreateObject("WScript.Shell").Run '
        '"""' + python_for_task() + '"" -m agentd.cli run", 0, False'
    )
    return (
        "' AgentLink daemon autostart - remove with: agentd uninstall\n"
        + line
        + "\n"
    )


def install() -> str:
    """Write the launcher (an OSError carries the system's words)."""
    target = launcher_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_launcher(), encoding="utf-8")
    return "the daemon starts at logon"


def uninstall() -> str:
    """Remove the launcher; removing a missing launcher is a success."""
    target = launcher_path()
    if target.exists():
        target.unlink()
    return "the logon launcher was removed"


def installed() -> bool:
    return launcher_path().exists()
