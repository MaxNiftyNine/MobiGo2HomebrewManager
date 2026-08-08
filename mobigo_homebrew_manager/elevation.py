"""Request raw-device privileges once, before opening the GUI."""

from __future__ import annotations

import ctypes
import json
import os
import shlex
import subprocess
import sys


def ensure_elevated() -> bool:
    if os.environ.get("MOBIGO_MANAGER_NO_ELEVATE") == "1":
        return True
    if os.name == "nt":
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        parameters = subprocess.list2cmdline(sys.argv[1:])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, parameters, None, 1
        )
        if result <= 32:
            raise RuntimeError("Windows administrator launch was declined or failed")
        return False
    if os.geteuid() == 0:
        return True
    command = (
        [sys.executable, *sys.argv[1:]]
        if getattr(sys, "frozen", False)
        else [sys.executable, *sys.argv]
    )
    if sys.platform == "darwin":
        shell = " ".join(shlex.quote(item) for item in command)
        script = "do shell script " + json.dumps(shell) + " with administrator privileges"
        subprocess.Popen(["osascript", "-e", script])
    else:
        subprocess.Popen(["pkexec", *command])
    return False
