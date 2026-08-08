# MobiGo 2 Homebrew Manager

A cross-platform desktop manager for `.MBA` homebrew on the VTech MobiGo 2.
It keeps the original `.MBA` filenames, rebuilds the launcher's `INDEX.HB`
catalog, and offers a guarded full-file-tree view for advanced work.

The first-run installer is deliberately backup-first:

1. discover the regional `/BUNDLE/SY/*SY.MBA` name;
2. download the complete SY file and write a verified local recovery copy;
3. create `/HB`, upload the original as `/HB/SystemMenu.MBA`, and read it back;
4. create and verify `/HB/INDEX.HB` so the original menu remains launchable;
5. only then replace SY with the bundled `HomebrewLauncher.MBA`;
6. read the installed launcher back byte-for-byte, restoring the original SY
   automatically if the final write fails.

Do not unplug or power off the console during a transfer. The Manager never
guesses a regional SY filename and protects `SystemMenu.MBA` from normal
deletion.

## Run from source

Python 3.10+ and Tk are required. On Linux, install your distribution's Tk
package (often `python3-tk`) first.

```sh
python3 -m pip install -r requirements.txt
python3 -m mobigo_homebrew_manager
```

The Manager uses the firmware's private filesystem mailbox through the small
FAT16 USB transport partition. Packaged releases request administrator/root
permission, temporarily dismount the transport volume, and read every write
back through the firmware before reporting success. Linux discovery expects
`lsblk`, `umount`, and `udisksctl`.

## Build and test

```sh
python3 -m unittest discover -s tests
python3 scripts/build.py
```

The bundled `assets/HomebrewLauncher.MBA` is built from the clean-room source
in the `examples/homebrew_launcher` directory of
[MobiGo2StarterProject](https://github.com/MaxNiftyNine/MobiGo2StarterProject).
