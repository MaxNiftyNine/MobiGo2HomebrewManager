"""Safe, testable Homebrew Manager operations independent of the GUI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Protocol

from .catalog import CatalogEntry, MAX_ENTRIES, decode, encode
from .mba import require_role


HB_DIRECTORY = "/HB"
CATALOG_PATH = "/HB/INDEX.HB"
SYSTEM_BACKUP_NAME = "System.MBA"
DMODE_PATH = "/ETC/DMODE"


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    size: int
    is_directory: bool


class RemoteFS(Protocol):
    def listdir(self, path: str) -> list[RemoteEntry]: ...
    def read_file(self, path: str) -> bytes: ...
    def write_file(self, path: str, data: bytes) -> None: ...
    def delete(self, path: str) -> None: ...
    def mkdir(self, path: str) -> None: ...
    def rmdir(self, path: str) -> None: ...
    def stat_size(self, path: str) -> int | None: ...


class ManagerError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_filename(filename: str) -> str:
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ManagerError("filename must be one plain device filename")
    try:
        encoded = filename.encode("ascii")
    except UnicodeEncodeError as error:
        raise ManagerError("MobiGo filenames must be ASCII") from error
    if len(encoded) > 12:
        raise ManagerError(
            "MobiGo directory listings preserve at most 12 filename characters"
        )
    if len(("A:\\HB\\" + filename).encode("ascii")) > 41:
        raise ManagerError("filename is too long for the MobiGo launch API")
    return filename


def discover_system_path(fs: RemoteFS) -> str:
    matches = [
        item.name
        for item in fs.listdir("/BUNDLE/SY")
        if not item.is_directory and item.name.upper().endswith("SY.MBA")
    ]
    if len(matches) != 1:
        raise ManagerError(
            f"expected exactly one regional SY.MBA, found {len(matches)}"
        )
    return "/BUNDLE/SY/" + matches[0]


def list_homebrew(fs: RemoteFS) -> list[RemoteEntry]:
    if fs.stat_size(HB_DIRECTORY) is None:
        return []
    return sorted(
        (
            item for item in fs.listdir(HB_DIRECTORY)
            if not item.is_directory and item.name.upper().endswith(".MBA")
        ),
        key=lambda item: item.name.casefold(),
    )


def rebuild_catalog(fs: RemoteFS) -> list[CatalogEntry]:
    apps = list_homebrew(fs)
    if len(apps) > MAX_ENTRIES:
        raise ManagerError(
            f"launcher supports {MAX_ENTRIES} apps; device has {len(apps)} in /HB"
        )
    entries = [
        CatalogEntry("A:\\HB\\" + item.name, item.name)
        for item in apps
    ]
    data = encode(entries)
    fs.write_file(CATALOG_PATH, data)
    if fs.read_file(CATALOG_PATH) != data:
        raise ManagerError("INDEX.HB read-back verification failed")
    if decode(data) != entries:
        raise AssertionError("catalog encoder did not round-trip")
    return entries


def _atomic_backup(directory: Path, filename: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = directory / f"{stamp}-{filename}"
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    if temporary.read_bytes() != data:
        temporary.unlink(missing_ok=True)
        raise ManagerError("local system-menu backup verification failed")
    os.replace(temporary, destination)
    return destination


@dataclass(frozen=True)
class InstallResult:
    system_path: str
    local_backup: Path
    original_sha256: str
    launcher_sha256: str


@dataclass(frozen=True)
class UninstallResult:
    system_path: str
    local_backup: Path
    restored_sha256: str


def install_launcher(
    fs: RemoteFS,
    launcher: bytes,
    backup_directory: Path,
) -> InstallResult:
    """Backup SY twice, verify both copies, then replace it transactionally."""
    require_role(launcher, "SY")
    system_path = discover_system_path(fs)
    original = fs.read_file(system_path)
    require_role(original, "SY")
    if original == launcher:
        raise ManagerError("HomebrewLauncher.MBA is already installed")

    local_backup = _atomic_backup(backup_directory, PurePosixPath(system_path).name, original)
    if fs.stat_size(HB_DIRECTORY) is None:
        fs.mkdir(HB_DIRECTORY)
        if fs.stat_size(HB_DIRECTORY) is None:
            raise ManagerError("device did not publish /HB after directory creation; SY was untouched")
    backup_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    fs.write_file(backup_path, original)
    if fs.read_file(backup_path) != original:
        raise ManagerError(f"{backup_path} backup verification failed; SY was untouched")
    rebuild_catalog(fs)

    try:
        fs.write_file(system_path, launcher)
        if fs.read_file(system_path) != launcher:
            raise ManagerError("launcher read-back verification failed")
    except Exception as install_error:
        try:
            fs.write_file(system_path, original)
            restored = fs.read_file(system_path)
        except Exception as restore_error:
            raise ManagerError(
                "launcher install failed and automatic SY restore also failed; "
                f"keep the device powered and use {local_backup}: {restore_error}"
            ) from install_error
        if restored != original:
            raise ManagerError(
                f"launcher install failed and SY restore did not verify; backup is {local_backup}"
            ) from install_error
        raise ManagerError("launcher install failed; original SY was restored") from install_error

    return InstallResult(
        system_path,
        local_backup,
        digest(original),
        digest(launcher),
    )


def add_homebrew(fs: RemoteFS, filename: str, data: bytes, *, overwrite: bool = False) -> str:
    filename = _validate_filename(filename)
    if not filename.upper().endswith(".MBA"):
        raise ManagerError("homebrew filename must retain its .MBA extension")
    from .mba import inspect
    inspect(data)
    if fs.stat_size(HB_DIRECTORY) is None:
        fs.mkdir(HB_DIRECTORY)
        if fs.stat_size(HB_DIRECTORY) is None:
            raise ManagerError("device did not publish /HB after directory creation")
    target = HB_DIRECTORY + "/" + filename
    if fs.stat_size(target) is not None and not overwrite:
        raise ManagerError(f"{filename} already exists")
    fs.write_file(target, data)
    if fs.read_file(target) != data:
        raise ManagerError(f"upload verification failed for {target}")
    rebuild_catalog(fs)
    return target


def delete_homebrew(fs: RemoteFS, filename: str) -> None:
    filename = _validate_filename(filename)
    if filename.casefold() == SYSTEM_BACKUP_NAME.casefold():
        raise ManagerError(
            f"{SYSTEM_BACKUP_NAME} can only be removed by 'Delete all homebrew and exit'"
        )
    path = HB_DIRECTORY + "/" + filename
    if fs.stat_size(path) is None:
        raise ManagerError(f"homebrew does not exist: {filename}")
    fs.delete(path)
    if fs.stat_size(path) is not None:
        raise ManagerError(f"device still reports {path} after deletion")
    rebuild_catalog(fs)


def _remove_tree(fs: RemoteFS, path: str) -> None:
    entries = list(fs.listdir(path))
    entries.sort(key=lambda item: item.name.casefold() == SYSTEM_BACKUP_NAME.casefold())
    for entry in entries:
        child = path.rstrip("/") + "/" + entry.name
        if entry.is_directory:
            _remove_tree(fs, child)
        else:
            fs.delete(child)
            if fs.stat_size(child) is not None:
                raise ManagerError(f"device still reports {child} after deletion")
    fs.rmdir(path)
    if fs.stat_size(path) is not None:
        raise ManagerError(f"device still reports {path} after directory removal")


def uninstall_homebrew(
    fs: RemoteFS,
    backup_directory: Path,
) -> UninstallResult:
    """Restore original SY, then remove /HB only after restoration verifies."""
    system_path = discover_system_path(fs)
    recovery_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    if fs.stat_size(recovery_path) is None:
        raise ManagerError(
            f"cannot uninstall without the verified {recovery_path} recovery copy"
        )
    original = fs.read_file(recovery_path)
    active = fs.read_file(system_path)
    require_role(original, "SY")
    require_role(active, "SY")
    local_backup = _atomic_backup(
        backup_directory,
        "uninstall-" + PurePosixPath(system_path).name,
        original,
    )

    if active != original:
        try:
            fs.write_file(system_path, original)
            if fs.read_file(system_path) != original:
                raise ManagerError("restored system-menu read-back verification failed")
        except Exception as restore_error:
            try:
                fs.write_file(system_path, active)
                rolled_back = fs.read_file(system_path)
            except Exception as rollback_error:
                raise ManagerError(
                    "system-menu restore failed and launcher rollback also failed; "
                    f"keep the device powered and use {local_backup}: {rollback_error}"
                ) from restore_error
            if rolled_back != active:
                raise ManagerError(
                    f"system-menu restore and launcher rollback did not verify; use {local_backup}"
                ) from restore_error
            raise ManagerError("system-menu restore failed; active launcher was restored") from restore_error

    if fs.read_file(system_path) != original:
        raise ManagerError("original system menu is not active; /HB was left untouched")
    _remove_tree(fs, HB_DIRECTORY)
    return UninstallResult(system_path, local_backup, digest(original))


def rename_file(fs: RemoteFS, source: str, destination: str) -> None:
    """Portable verified-copy rename; delete happens only after byte equality."""
    if not source.startswith("/") or not destination.startswith("/"):
        raise ManagerError("rename paths must be absolute")
    data = fs.read_file(source)
    if fs.stat_size(destination) is not None:
        raise ManagerError(f"rename destination already exists: {destination}")
    fs.write_file(destination, data)
    if fs.read_file(destination) != data:
        raise ManagerError("rename copy did not verify; source was kept")
    fs.delete(source)
    if fs.stat_size(source) is not None:
        raise ManagerError("rename destination verified, but source deletion failed")
    if source.upper().startswith("/HB/") or destination.upper().startswith("/HB/"):
        rebuild_catalog(fs)


def set_developer_mode(fs: RemoteFS, enabled: bool) -> bool:
    """Set D-mode and return whether retail firmware now requires a reboot."""
    if enabled:
        try:
            fs.write_file(DMODE_PATH, b"")
        except Exception as error:
            # Retail firmware creates the zero-byte marker, then invalidates
            # the mailbox handle and reports -1 while closing it.  No further
            # filesystem command is reliable until the console reboots.
            if "closing file failed (device status -1)" in str(error):
                return True
            raise
        if fs.stat_size(DMODE_PATH) != 0:
            raise ManagerError("DMODE creation did not verify")
        return False
    elif fs.stat_size(DMODE_PATH) is not None:
        fs.delete(DMODE_PATH)
        if fs.stat_size(DMODE_PATH) is not None:
            raise ManagerError("DMODE deletion did not verify")
    return False
