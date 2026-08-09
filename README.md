# MobiGo 2 Homebrew Manager

A cross-platform Python GUI for managing `.MBA` homebrew on a VTech MobiGo 2.
It preserves `.MBA` filenames, maintains the launcher's `/HB/INDEX.HB` catalog,
and provides a guarded full-filesystem view for advanced work.

The Manager is distributed as Python source, not as a platform-specific `.app`
or `.exe`. Run it from a terminal with raw-device privileges so errors remain
visible and macOS grants disk access to the same terminal process.

## Run the GUI

Python 3.10+ and Tk are required. Drag-and-drop uses the optional
`tkinterdnd2` dependency; the file picker works without it.

### macOS

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python mobigo_manager.py
```

If macOS refuses `/dev/rdisk*`, give Terminal Full Disk Access in System
Settings, reconnect the MobiGo, and retry. Do not launch the script through an
AppleScript password dialog: that elevated process does not inherit Terminal's
raw-disk privacy permission.

### Linux

Install Tk with the distribution package manager, create the venv as above,
then preserve the graphical-session variables:

```sh
sudo --preserve-env=DISPLAY,XAUTHORITY .venv/bin/python mobigo_manager.py
```

### Windows

Create the environment in a normal PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Then open PowerShell **as Administrator** and run:

```powershell
.venv\Scripts\python mobigo_manager.py
```

## Backup-first launcher installation

The first-run installer performs these operations in order:

1. discover the device's actual regional `/BUNDLE/SY/*SY.MBA` filename;
2. download SY and fsync a byte-verified local recovery backup;
3. create and rediscover `/HB` through the device's root directory;
4. upload and read back `/HB/System.MBA`;
5. create and verify `/HB/INDEX.HB` so the original menu remains launchable;
6. replace SY with `HomebrewLauncher.MBA` and read it back byte-for-byte;
7. automatically restore the original SY if final installation fails.

Never unplug or power off the console during a transfer. Advanced mode allows
upload, rename, and delete operations on the active SY path. Deleting or
corrupting SY can make the console unbootable, so preserve an independent
recovery backup first.

`System.MBA` cannot be deleted by the normal or Advanced delete buttons. Use
**Delete all homebrew and exit** instead. That transaction creates another
local recovery backup, restores `System.MBA` to the discovered regional SY
path, verifies the restored system menu byte-for-byte, and only then deletes
the contents of `/HB`, removes the `/HB` directory, and closes the Manager. If
restoration fails, it rolls the active launcher back and leaves `/HB` intact.

## Physical-hardware evidence

On 2026-08-08 the Manager was tested against a US MobiGo 2 using dynamically
discovered regional SY storage. Retail firmware behavior established by that test:

- a missing path may return successful `stat` status with size zero, so the
  Manager proves existence through path type plus the parent directory listing;
- `/HB` creation is published correctly and can be rediscovered at the root;
- empty-directory removal uses retail mailbox command `0x0B`, verified with a
  disposable directory before being used by the uninstall transaction;
- directory enumeration preserves at most 12 filename characters, so uploads
  longer than that are rejected instead of being silently truncated;
- file writes require an even byte length; odd uploads are rejected before any
  device write;
- enabling `/ETC/DMODE` creates the marker but returns `-1` while closing the
  empty file, after which the console must be unplugged and rebooted.

The maintained physical suite is intentionally explicit:

```sh
sudo python3 scripts/hardware_test.py --device /dev/disk5
```

Add `--test-dmode` only when you are ready to reboot immediately afterward.
Add `--install-launcher` only after preserving an independent SY backup.

## Development tests

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q mobigo_manager.py mobigo_homebrew_manager scripts tests
```

The bundled `assets/HomebrewLauncher.MBA` is built from the clean-room launcher
source in the
[MobiGo2StarterProject](https://github.com/MaxNiftyNine/MobiGo2StarterProject).
