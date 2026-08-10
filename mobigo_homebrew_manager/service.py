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
SYSTEM_PATH = "/USENG/MM.MBA"
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
    """Return the exact main-menu slot used for launcher installation."""
    matches = [
        item.name
        for item in fs.listdir("/USENG")
        if not item.is_directory and item.name.upper() == "MM.MBA"
    ]
    if len(matches) != 1:
        raise ManagerError(
            f"expected exactly one {SYSTEM_PATH}, found {len(matches)} matching file(s)"
        )
    return "/USENG/" + matches[0]


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


def list_catalog(fs: RemoteFS) -> list[CatalogEntry]:
    if fs.stat_size(CATALOG_PATH) is None:
        return []
    try:
        return decode(fs.read_file(CATALOG_PATH))
    except ValueError as error:
        raise ManagerError(f"INDEX.HB is invalid: {error}") from error


def _catalog_metadata(fs: RemoteFS) -> dict[str, CatalogEntry]:
    if fs.stat_size(CATALOG_PATH) is None:
        return {}
    try:
        raw = fs.read_file(CATALOG_PATH)
        entries = decode(raw)
    except (ManagerError, OSError, ValueError):
        return {}
    if raw[:4] == b"HB01":
        return {
            PurePosixPath(item.path.replace("\\", "/")).name.casefold():
                _default_metadata(
                    PurePosixPath(item.path.replace("\\", "/")).name
                )
            for item in entries
        }
    return {
        PurePosixPath(item.path.replace("\\", "/")).name.casefold(): item
        for item in entries
    }


def _default_metadata(filename: str) -> CatalogEntry:
    if filename.casefold() == SYSTEM_BACKUP_NAME.casefold():
        return CatalogEntry(
            "unused", "System Menu", "Original MobiGo menu", "VTech", 5
        )
    return CatalogEntry("unused", PurePosixPath(filename).stem, icon=1)


def rebuild_catalog(
    fs: RemoteFS,
    metadata_overrides: dict[str, CatalogEntry] | None = None,
) -> list[CatalogEntry]:
    apps = list_homebrew(fs)
    if len(apps) > MAX_ENTRIES:
        raise ManagerError(
            f"launcher supports {MAX_ENTRIES} apps; device has {len(apps)} in /HB"
        )
    metadata = _catalog_metadata(fs)
    if metadata_overrides:
        metadata.update(
            {name.casefold(): value for name, value in metadata_overrides.items()}
        )
    entries = []
    for item in apps:
        detail = metadata.get(item.name.casefold(), _default_metadata(item.name))
        entries.append(
            CatalogEntry(
                "A:\\HB\\" + item.name,
                detail.title,
                detail.description,
                detail.author,
                detail.icon,
                detail.flags,
            )
        )
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
    """Back up /USENG/MM.MBA twice, then replace it transactionally."""
    require_role(launcher, "SY")
    system_path = discover_system_path(fs)
    original = fs.read_file(system_path)
    if original == launcher:
        raise ManagerError("HomebrewLauncher.MBA is already installed")

    local_backup = _atomic_backup(backup_directory, PurePosixPath(system_path).name, original)
    if fs.stat_size(HB_DIRECTORY) is None:
        fs.mkdir(HB_DIRECTORY)
        if fs.stat_size(HB_DIRECTORY) is None:
            raise ManagerError("device did not publish /HB after directory creation; /USENG/MM.MBA was untouched")
    backup_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    fs.write_file(backup_path, original)
    if fs.read_file(backup_path) != original:
        raise ManagerError(f"{backup_path} backup verification failed; /USENG/MM.MBA was untouched")
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
                "launcher install failed and automatic MM.MBA restore also failed; "
                f"keep the device powered and use {local_backup}: {restore_error}"
            ) from install_error
        if restored != original:
            raise ManagerError(
                f"launcher install failed and MM.MBA restore did not verify; backup is {local_backup}"
            ) from install_error
        raise ManagerError("launcher install failed; original MM.MBA was restored") from install_error

    return InstallResult(
        system_path,
        local_backup,
        digest(original),
        digest(launcher),
    )


def install_or_update_launcher(
    fs: RemoteFS,
    launcher: bytes,
    backup_directory: Path,
) -> InstallResult:
    """Install a launcher, or update one without replacing the original recovery MBA."""
    require_role(launcher, "SY")
    recovery_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    if fs.stat_size(recovery_path) is None:
        return install_launcher(fs, launcher, backup_directory)

    system_path = discover_system_path(fs)
    active = fs.read_file(system_path)
    original = fs.read_file(recovery_path)
    require_role(active, "SY")
    if active == launcher:
        raise ManagerError("HomebrewLauncher.MBA is already up to date")

    # Preserve the known recovery copy both remotely and locally. The active
    # launcher is separately captured so a failed update can be rolled back.
    local_backup = _atomic_backup(
        backup_directory,
        "recovery-" + PurePosixPath(system_path).name,
        original,
    )
    if fs.read_file(recovery_path) != original:
        raise ManagerError(f"{recovery_path} recovery copy changed; /USENG/MM.MBA was untouched")
    rebuild_catalog(fs)

    try:
        fs.write_file(system_path, launcher)
        if fs.read_file(system_path) != launcher:
            raise ManagerError("updated launcher read-back verification failed")
    except Exception as update_error:
        try:
            fs.write_file(system_path, active)
            rolled_back = fs.read_file(system_path)
        except Exception as rollback_error:
            raise ManagerError(
                "launcher update failed and rollback also failed; keep the device "
                f"powered and use {local_backup}: {rollback_error}"
            ) from update_error
        if rolled_back != active:
            raise ManagerError(
                f"launcher update and rollback did not verify; recovery is {local_backup}"
            ) from update_error
        raise ManagerError("launcher update failed; previous launcher was restored") from update_error

    return InstallResult(
        system_path,
        local_backup,
        digest(original),
        digest(launcher),
    )


def add_homebrew(
    fs: RemoteFS,
    filename: str,
    data: bytes,
    *,
    overwrite: bool = False,
    metadata: CatalogEntry | None = None,
) -> str:
    filename = _validate_filename(filename)
    if not filename.upper().endswith(".MBA"):
        raise ManagerError("homebrew filename must retain its .MBA extension")
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
    overrides = {filename: metadata} if metadata is not None else None
    rebuild_catalog(fs, overrides)
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
    """Restore original /USENG/MM.MBA, then remove /HB after verification."""
    system_path = discover_system_path(fs)
    recovery_path = HB_DIRECTORY + "/" + SYSTEM_BACKUP_NAME
    if fs.stat_size(recovery_path) is None:
        raise ManagerError(
            f"cannot uninstall without the verified {recovery_path} recovery copy"
        )
    original = fs.read_file(recovery_path)
    active = fs.read_file(system_path)
    if active != original:
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
    prior_metadata = _catalog_metadata(fs) if source.upper().startswith("/HB/") else {}
    if fs.stat_size(destination) is not None:
        raise ManagerError(f"rename destination already exists: {destination}")
    fs.write_file(destination, data)
    if fs.read_file(destination) != data:
        raise ManagerError("rename copy did not verify; source was kept")
    fs.delete(source)
    if fs.stat_size(source) is not None:
        raise ManagerError("rename destination verified, but source deletion failed")
    if source.upper().startswith("/HB/") or destination.upper().startswith("/HB/"):
        source_name = PurePosixPath(source).name
        destination_name = PurePosixPath(destination).name
        detail = prior_metadata.get(source_name.casefold())
        rebuild_catalog(
            fs,
            {destination_name: detail} if detail is not None else None,
        )


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
