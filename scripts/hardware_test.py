#!/usr/bin/env python3
"""Exercise Homebrew Manager operations against a connected physical MobiGo 2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobigo_homebrew_manager.catalog import decode  # noqa: E402
from mobigo_homebrew_manager.device import DeviceSession  # noqa: E402
from mobigo_homebrew_manager.service import (  # noqa: E402
    CATALOG_PATH,
    DMODE_PATH,
    HB_DIRECTORY,
    SYSTEM_BACKUP_NAME,
    add_homebrew,
    delete_homebrew,
    discover_system_path,
    install_launcher,
    list_homebrew,
    rebuild_catalog,
    rename_file,
    set_developer_mode,
    uninstall_homebrew,
)


TEMP_MBA = "MgrTest.MBA"
RENAMED_MBA = "MgrTest2.MBA"
TEMP_DATA = "/HB/ADVTEST.DAT"
RENAMED_DATA = "/HB/ADVTEST2.DAT"
DATA_PAYLOAD = b"MobiGo Manager advanced file test!\r\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk(fs, path: str = "/", *, limit: int = 1024) -> list[str]:
    output: list[str] = []
    for entry in fs.listdir(path):
        child = path.rstrip("/") + "/" + entry.name
        output.append(child + ("/" if entry.is_directory else ""))
        if len(output) > limit:
            raise RuntimeError("filesystem traversal exceeded safety limit")
        if entry.is_directory:
            output.extend(walk(fs, child, limit=limit - len(output)))
    return output


def require_absent(fs, path: str) -> None:
    if fs.stat_size(path) is not None:
        raise RuntimeError(f"refusing pre-existing hardware-test path {path}")


def reversible_tests(fs, launcher: bytes, *, test_dmode: bool) -> dict[str, object]:
    created_hb = fs.stat_size(HB_DIRECTORY) is None
    if created_hb:
        fs.mkdir(HB_DIRECTORY)
    if fs.stat_size(HB_DIRECTORY) is None:
        raise RuntimeError("firmware did not publish /HB")
    root_names = [entry.name for entry in fs.listdir("/")]
    if "HB" not in root_names:
        raise RuntimeError("/HB did not appear in the root directory listing")

    paths = (
        "/HB/" + TEMP_MBA,
        "/HB/" + RENAMED_MBA,
        TEMP_DATA,
        RENAMED_DATA,
    )
    for path in paths:
        require_absent(fs, path)

    original_dmode = fs.stat_size(DMODE_PATH) is not None
    original_catalog = (
        fs.read_file(CATALOG_PATH) if fs.stat_size(CATALOG_PATH) is not None else None
    )
    report: dict[str, object] = {
        "created_hb": created_hb,
        "root_contains_hb": True,
        "original_dmode": original_dmode,
    }
    try:
        add_homebrew(fs, TEMP_MBA, launcher)
        uploaded = fs.read_file("/HB/" + TEMP_MBA)
        if uploaded != launcher:
            raise RuntimeError("temporary MBA download did not match upload")
        report["mba_upload_sha256"] = sha256(uploaded)

        rename_file(fs, "/HB/" + TEMP_MBA, "/HB/" + RENAMED_MBA)
        if fs.read_file("/HB/" + RENAMED_MBA) != launcher:
            raise RuntimeError("renamed MBA did not verify")
        report["mba_rename"] = True
        if RENAMED_MBA not in [entry.name for entry in list_homebrew(fs)]:
            raise RuntimeError("renamed MBA was absent from homebrew listing")

        delete_homebrew(fs, RENAMED_MBA)
        if fs.stat_size("/HB/" + RENAMED_MBA) is not None:
            raise RuntimeError("temporary MBA remained after delete")
        report["mba_delete"] = True

        fs.write_file(TEMP_DATA, DATA_PAYLOAD)
        if fs.read_file(TEMP_DATA) != DATA_PAYLOAD:
            raise RuntimeError("advanced upload read-back failed")
        rename_file(fs, TEMP_DATA, RENAMED_DATA)
        if fs.read_file(RENAMED_DATA) != DATA_PAYLOAD:
            raise RuntimeError("advanced rename read-back failed")
        fs.delete(RENAMED_DATA)
        if fs.stat_size(RENAMED_DATA) is not None:
            raise RuntimeError("advanced delete verification failed")
        report["advanced_file_lifecycle"] = True

        tree = walk(fs)
        if "/HB/" not in tree:
            raise RuntimeError("full tree traversal did not include /HB")
        report["tree_entries"] = len(tree)
    finally:
        for path in paths:
            if fs.stat_size(path) is not None:
                fs.delete(path)
        if original_catalog is None:
            if fs.stat_size(CATALOG_PATH) is not None:
                fs.delete(CATALOG_PATH)
        else:
            fs.write_file(CATALOG_PATH, original_catalog)
            if fs.read_file(CATALOG_PATH) != original_catalog:
                raise RuntimeError("original INDEX.HB restoration failed")

    # Enabling D-mode is deliberately last: retail firmware creates the marker
    # but invalidates its USB mailbox until the console reboots.
    if test_dmode:
        if original_dmode:
            set_developer_mode(fs, False)
            if fs.stat_size(DMODE_PATH) is not None:
                raise RuntimeError("developer-mode disable did not verify")
            reboot_required = set_developer_mode(fs, True)
            report["dmode_toggle_and_restore"] = True
            report["dmode_reboot_required"] = reboot_required
        else:
            reboot_required = set_developer_mode(fs, True)
            report["dmode_enable"] = True
            report["dmode_reboot_required"] = reboot_required
            report["dmode_restore_after_reboot"] = "disable with the Manager"
    else:
        report["dmode_test"] = "skipped"

    return report


def install_tests(fs, launcher: bytes, backup_directory: Path) -> dict[str, object]:
    system_path = discover_system_path(fs)
    original = fs.read_file(system_path)
    result = install_launcher(fs, launcher, backup_directory)
    installed = fs.read_file(system_path)
    remote_backup = fs.read_file(HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME)
    catalog = decode(fs.read_file(CATALOG_PATH))
    if installed != launcher:
        raise RuntimeError("installed /USENG/MM.MBA does not match HomebrewLauncher.MBA")
    if remote_backup != original:
        raise RuntimeError(f"remote {SYSTEM_BACKUP_NAME} does not match original /USENG/MM.MBA")
    if not any(entry.label == SYSTEM_BACKUP_NAME for entry in catalog):
        raise RuntimeError(f"INDEX.HB does not contain {SYSTEM_BACKUP_NAME}")
    if result.local_backup.read_bytes() != original:
        raise RuntimeError("local recovery backup does not match original /USENG/MM.MBA")
    return {
        "system_path": system_path,
        "original_sha256": sha256(original),
        "launcher_sha256": sha256(installed),
        "remote_backup_sha256": sha256(remote_backup),
        "local_backup": str(result.local_backup),
        "catalog_entries": [entry.label for entry in catalog],
    }


def verify_installed(fs, launcher: bytes) -> dict[str, object]:
    system_path = discover_system_path(fs)
    installed = fs.read_file(system_path)
    backup_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    remote_backup = fs.read_file(backup_path)
    catalog = decode(fs.read_file(CATALOG_PATH))
    if installed != launcher:
        raise RuntimeError("active /USENG/MM.MBA does not match HomebrewLauncher.MBA")
    if remote_backup == launcher:
        raise RuntimeError("recovery copy unexpectedly matches the launcher")
    if not any(entry.label == SYSTEM_BACKUP_NAME for entry in catalog):
        raise RuntimeError(f"INDEX.HB does not contain {SYSTEM_BACKUP_NAME}")
    return {
        "system_path": system_path,
        "launcher_sha256": sha256(installed),
        "remote_backup_sha256": sha256(remote_backup),
        "catalog_entries": [entry.label for entry in catalog],
    }


def uninstall_tests(fs, backup_directory: Path) -> dict[str, object]:
    recovery_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    expected = fs.read_file(recovery_path)
    result = uninstall_homebrew(fs, backup_directory)
    restored = fs.read_file(result.system_path)
    if restored != expected:
        raise RuntimeError("uninstall did not restore the expected system menu")
    if fs.stat_size(HB_DIRECTORY) is not None:
        raise RuntimeError("/HB still exists after uninstall")
    if result.local_backup.read_bytes() != expected:
        raise RuntimeError("uninstall local backup does not match restored /USENG/MM.MBA")
    return {
        "system_path": result.system_path,
        "restored_sha256": sha256(restored),
        "hb_removed": True,
        "local_backup": str(result.local_backup),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=None, help="physical disk override")
    parser.add_argument(
        "--launcher", type=Path, default=ROOT / "assets" / "HomebrewLauncher.MBA"
    )
    parser.add_argument("--install-launcher", action="store_true")
    parser.add_argument("--verify-installed", action="store_true")
    parser.add_argument("--uninstall-homebrew", action="store_true")
    parser.add_argument("--skip-reversible", action="store_true")
    parser.add_argument(
        "--test-dmode", action="store_true",
        help="toggle D-mode last; enabling requires a physical reboot",
    )
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=Path.home() / "Documents" / "MobiGo 2 Backups",
    )
    parser.add_argument(
        "--report", type=Path, default=Path("/tmp/mobigo-manager-hardware-test.json")
    )
    args = parser.parse_args()
    if os.name != "nt" and os.geteuid() != 0:
        parser.error("run this hardware test with sudo")

    launcher = args.launcher.read_bytes()
    report: dict[str, object] = {}
    if not args.skip_reversible:
        with DeviceSession(args.device) as fs:
            report["reversible"] = reversible_tests(
                fs, launcher, test_dmode=args.test_dmode
            )
    if args.uninstall_homebrew:
        with DeviceSession(args.device) as fs:
            report["uninstall"] = uninstall_tests(fs, args.backup_directory)
    if args.install_launcher:
        with DeviceSession(args.device) as fs:
            report["install"] = install_tests(fs, launcher, args.backup_directory)
    if args.verify_installed:
        with DeviceSession(args.device) as fs:
            report["installed"] = verify_installed(fs, launcher)

    args.report.write_text(json.dumps(report, indent=2) + "\n")
    os.chmod(args.report, 0o644)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
