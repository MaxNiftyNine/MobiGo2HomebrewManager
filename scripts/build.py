#!/usr/bin/env python3
from pathlib import Path
import os
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
separator = os.pathsep
command = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--windowed", "--optimize", "2",
    "--name", "MobiGo2HomebrewManager",
    "--add-data", f"{ROOT / 'assets'}{separator}assets",
    "--collect-all", "tkinterdnd2",
]
if platform.system() != "Darwin":
    command.append("--onefile")
command.append(str(ROOT / "mobigo_homebrew_manager" / "__main__.py"))
subprocess.run(command, cwd=ROOT, check=True)
