"""Privilege checks for the source-based Manager launcher."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


def is_elevated() -> bool:
    if os.name == "nt":
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


def require_elevated() -> None:
    if is_elevated():
        return
    if os.name == "nt":
        raise RuntimeError(
            "Administrator access is required. Open PowerShell as Administrator, "
            "then run: py mobigo_manager.py"
        )
    if sys.platform == "darwin":
        raise RuntimeError(
            "Raw MobiGo access is required. Run from Terminal with: "
            "sudo python3 mobigo_manager.py"
        )
    raise RuntimeError(
        "Raw MobiGo access is required. Run from a graphical terminal with: "
        "sudo --preserve-env=DISPLAY,XAUTHORITY python3 mobigo_manager.py"
    )


def invoking_user_home() -> Path:
    """Return the desktop user's home even while the Manager runs via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    return Path.home()
