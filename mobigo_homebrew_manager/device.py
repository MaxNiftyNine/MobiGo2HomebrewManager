"""Cross-platform MobiGo 2 USB mailbox transport."""

from __future__ import annotations

import glob
import json
import os
import plistlib
import re
import struct
import subprocess
import sys
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .service import RemoteEntry


BLOCK_SIZE = 512
MAX_TRANSFER = 0x10000
MAILBOX_ADDRESS = 0x00280000
READ_DATA_LBA = 0x3B00
WRITE_DATA_LBA = 0x3C00
READ_SETUP_LBA = 0x3D28
WRITE_SETUP_LBA = 0x3D2A
DEVICE_MODEL = "USB-MSDC DISK A"
MODE_READ = 1
MODE_WRITE = 2


class MobiGoError(RuntimeError):
    pass


class PosixBackend:
    def __init__(self, path: str):
        self.path = path
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError as error:
            raise MobiGoError(
                f"permission denied opening {path}; run the Manager as administrator/root"
            ) from error
        except OSError as error:
            raise MobiGoError(f"cannot open {path}: {error}") from error

    def read_at(self, offset: int, size: int) -> bytes:
        os.lseek(self.fd, offset, os.SEEK_SET)
        output = bytearray()
        while len(output) < size:
            chunk = os.read(self.fd, size - len(output))
            if not chunk:
                raise MobiGoError(f"short raw read at 0x{offset:x}")
            output += chunk
        return bytes(output)

    def write_at(self, offset: int, data: bytes) -> None:
        os.lseek(self.fd, offset, os.SEEK_SET)
        position = 0
        while position < len(data):
            count = os.write(self.fd, data[position:])
            if count <= 0:
                raise MobiGoError(f"short raw write at 0x{offset:x}")
            position += count

    def close(self) -> None:
        os.close(self.fd)


class WindowsBackend:
    def __init__(self, path: str):
        try:
            import win32con
            import win32file
            import winioctlcon
        except ImportError as error:
            raise MobiGoError("Windows USB access requires the bundled pywin32 runtime") from error
        self.win32con = win32con
        self.win32file = win32file
        self.handle = win32file.CreateFile(
            path,
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            win32con.OPEN_EXISTING,
            0,
            None,
        )
        for operation in (
            winioctlcon.FSCTL_LOCK_VOLUME,
            winioctlcon.FSCTL_DISMOUNT_VOLUME,
            winioctlcon.FSCTL_ALLOW_EXTENDED_DASD_IO,
        ):
            try:
                win32file.DeviceIoControl(self.handle, operation, None, 0)
            except Exception:
                pass

    def read_at(self, offset: int, size: int) -> bytes:
        self.win32file.SetFilePointer(self.handle, offset, self.win32con.FILE_BEGIN)
        _, data = self.win32file.ReadFile(self.handle, size)
        if len(data) != size:
            raise MobiGoError(f"short raw read at 0x{offset:x}")
        return data

    def write_at(self, offset: int, data: bytes) -> None:
        self.win32file.SetFilePointer(self.handle, offset, self.win32con.FILE_BEGIN)
        _, written = self.win32file.WriteFile(self.handle, data)
        if written not in (None, len(data)):
            raise MobiGoError(f"short raw write at 0x{offset:x}")

    def close(self) -> None:
        self.handle.Close()


class MailboxTransport:
    def __init__(self, backend):
        self.backend = backend
        self.reference_lba = self._fat_data_start()

    def _fat_data_start(self) -> int:
        boot = self.backend.read_at(0, BLOCK_SIZE)
        sector_size = struct.unpack_from("<H", boot, 11)[0]
        reserved = struct.unpack_from("<H", boot, 14)[0]
        fats = boot[16]
        root_entries = struct.unpack_from("<H", boot, 17)[0]
        sectors_per_fat = struct.unpack_from("<H", boot, 22)[0]
        if (
            sector_size != BLOCK_SIZE
            or not reserved
            or not fats
            or not sectors_per_fat
            or boot[54:62] != b"FAT16   "
            or boot[510:512] != b"\x55\xaa"
        ):
            raise MobiGoError("refusing unexpected transport-partition layout")
        root_sectors = (root_entries * 32 + BLOCK_SIZE - 1) // BLOCK_SIZE
        return reserved + fats * sectors_per_fat + root_sectors

    @staticmethod
    def _setup(size: int) -> bytes:
        blocks = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        return struct.pack(">I2sH", MAILBOX_ADDRESS, b"\x06\x00", blocks).ljust(
            BLOCK_SIZE, b"\0"
        )

    def _offset(self, lba: int) -> int:
        return (self.reference_lba + lba) * BLOCK_SIZE

    def read(self, size: int) -> bytes:
        if size <= 0 or size % BLOCK_SIZE:
            raise ValueError("mailbox reads must be positive 512-byte multiples")
        self.backend.write_at(self._offset(READ_SETUP_LBA), self._setup(size))
        return self.backend.read_at(self._offset(READ_DATA_LBA), size)

    def write(self, data: bytes) -> None:
        if not data or len(data) % BLOCK_SIZE:
            raise ValueError("mailbox writes must be positive 512-byte multiples")
        self.backend.write_at(self._offset(WRITE_SETUP_LBA), self._setup(len(data)))
        self.backend.write_at(self._offset(WRITE_DATA_LBA), data)


@dataclass(frozen=True)
class WireEntry:
    name: str
    size: int
    kind: int
    token: int


class MobiGoFS:
    def __init__(self, transport):
        self.transport = transport
        self._drive: str | None = None

    @staticmethod
    def _request(command: int) -> bytearray:
        request = bytearray(BLOCK_SIZE)
        struct.pack_into("<I", request, 0, command)
        return request

    def _path(self, request: bytearray, path: str, limit: int = 42) -> None:
        wire = path.replace("/", "\\")
        if wire.startswith("\\"):
            if self._drive is None:
                drive = self._simple(self._request(0x16), "get current drive")
                if not ord("A") <= drive <= ord("Z"):
                    raise MobiGoError(f"device returned invalid drive {drive}")
                self._drive = chr(drive)
            wire = self._drive + ":" + wire
        encoded = wire.encode("ascii")
        if not encoded or len(encoded) > limit:
            raise MobiGoError(f"device path is too long: {path}")
        request[4 : 4 + len(encoded)] = encoded

    def _exchange(self, request: bytes) -> bytes:
        self.transport.write(request)
        time.sleep(0.05)
        return self.transport.read(BLOCK_SIZE)

    @staticmethod
    def _status(response: bytes) -> int:
        return struct.unpack_from("<h", response, 0)[0]

    def _simple(self, request: bytes, operation: str) -> int:
        status = self._status(self._exchange(request))
        if status < 0:
            raise MobiGoError(f"{operation} failed (device status {status})")
        return status

    def path_type(self, path: str) -> int:
        request = self._request(0x10)
        self._path(request, path)
        status = self._status(self._exchange(request))
        return 0 if status < 0 else status

    def stat_size(self, path: str) -> int | None:
        request = self._request(9)
        self._path(request, path)
        response = self._exchange(request)
        if self._status(response) < 0:
            return None
        size = struct.unpack_from("<I", response, 4)[0]
        return None if size == 0xFFFFFFFF else size

    def open(self, path: str, mode: int) -> int:
        request = self._request(2)
        self._path(request, path)
        struct.pack_into("<H", request, 46, mode)
        return self._simple(request, f"opening {path}")

    def close(self, handle: int) -> None:
        request = self._request(5)
        struct.pack_into("<H", request, 4, handle)
        self._simple(request, "closing file")

    def seek(self, handle: int, offset: int) -> None:
        request = self._request(0x0C)
        struct.pack_into("<IH", request, 4, offset, handle)
        self._simple(request, "seeking file")

    def truncate(self, handle: int) -> None:
        request = self._request(0x0D)
        struct.pack_into("<H", request, 4, handle)
        self._simple(request, "truncating file")

    def read_handle(self, handle: int, size: int) -> bytes:
        if size == 0:
            return b""
        rounded = (size + BLOCK_SIZE - 1) & ~(BLOCK_SIZE - 1)
        request = self._request(3)
        struct.pack_into("<H", request, 4, handle)
        struct.pack_into("<I", request, 8, rounded)
        self.transport.write(request)
        output = bytearray()
        for offset in range(0, rounded, MAX_TRANSFER):
            output += self.transport.read(min(MAX_TRANSFER, rounded - offset))
        response = self.transport.read(BLOCK_SIZE)
        if self._status(response) < 0:
            raise MobiGoError("reading file data failed")
        return bytes(output[:size])

    def read_file(self, path: str) -> bytes:
        size = self.stat_size(path)
        if size is None:
            raise MobiGoError(f"remote file does not exist: {path}")
        handle = self.open(path, MODE_READ)
        try:
            self.seek(handle, 0)
            return self.read_handle(handle, size)
        finally:
            self.close(handle)

    def write_handle(self, handle: int, data: bytes) -> None:
        rounded = (len(data) + BLOCK_SIZE - 1) & ~(BLOCK_SIZE - 1)
        request = self._request(4)
        struct.pack_into("<H", request, 4, handle)
        struct.pack_into("<I", request, 8, rounded)
        self.transport.write(request)
        padded = data + bytes(rounded - len(data))
        for offset in range(0, rounded, MAX_TRANSFER):
            self.transport.write(padded[offset : offset + MAX_TRANSFER])
        time.sleep(0.05)
        if self._status(self.transport.read(BLOCK_SIZE)) < 0:
            raise MobiGoError("writing file data failed")

    def write_file(self, path: str, data: bytes) -> None:
        handle = self.open(path, MODE_WRITE)
        try:
            self.seek(handle, 0)
            self.truncate(handle)
            if data:
                self.write_handle(handle, data)
            self.seek(handle, len(data))
            self.truncate(handle)
        finally:
            self.close(handle)

    def delete(self, path: str) -> None:
        request = self._request(8)
        self._path(request, path)
        self._simple(request, f"deleting {path}")

    def mkdir(self, path: str) -> None:
        request = self._request(0x0A)
        self._path(request, path)
        self._simple(request, f"creating directory {path}")

    @staticmethod
    def _entries(page: bytes) -> Iterator[WireEntry]:
        for offset in range(0, BLOCK_SIZE - 27, 28):
            token = struct.unpack_from("<h", page, offset)[0]
            if token < 0:
                return
            name = page[offset + 4 : offset + 18].split(b"\0", 1)[0]
            yield WireEntry(
                name.decode("ascii", "replace"),
                struct.unpack_from("<I", page, offset + 24)[0],
                struct.unpack_from("<H", page, offset + 18)[0],
                token,
            )

    def listdir(self, path: str) -> list[RemoteEntry]:
        request = self._request(6)
        self._path(request, path, 30)
        page = self._exchange(request)
        output: list[RemoteEntry] = []
        while True:
            entries = list(self._entries(page))
            if not entries:
                return output
            output += [RemoteEntry(item.name, item.size, item.kind != 1) for item in entries]
            if len(entries) < 18:
                return output
            request = self._request(7)
            struct.pack_into("<i", request, 4, entries[-1].token)
            page = self._exchange(request)


class MountedMobiGoFS:
    """Filesystem view exported by retail firmware over USB mass storage.

    Learning Lodge performs directory management through this mounted view.
    Every write is flushed before the service layer performs its byte-for-byte
    read-back verification.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise MobiGoError(f"MobiGo mount is not available: {self.root}")

    def _local(self, remote: str) -> Path:
        wire = remote.replace("\\", "/")
        if len(wire) >= 2 and wire[1] == ":":
            wire = wire[2:]
        parts = [part for part in wire.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise MobiGoError(f"unsafe device path: {remote}")
        try:
            "".join(parts).encode("ascii")
        except UnicodeEncodeError as error:
            raise MobiGoError("MobiGo device paths must be ASCII") from error
        return self.root.joinpath(*parts)

    def listdir(self, path: str) -> list[RemoteEntry]:
        local = self._local(path)
        try:
            entries = list(local.iterdir())
        except OSError as error:
            raise MobiGoError(f"cannot list {path}: {error}") from error
        return sorted(
            [RemoteEntry(item.name, 0 if item.is_dir() else item.stat().st_size, item.is_dir())
             for item in entries],
            key=lambda item: item.name.casefold(),
        )

    def stat_size(self, path: str) -> int | None:
        local = self._local(path)
        try:
            return 0 if local.is_dir() else local.stat().st_size
        except FileNotFoundError:
            return None
        except OSError as error:
            raise MobiGoError(f"cannot stat {path}: {error}") from error

    def read_file(self, path: str) -> bytes:
        try:
            return self._local(path).read_bytes()
        except OSError as error:
            raise MobiGoError(f"cannot read {path}: {error}") from error

    def write_file(self, path: str, data: bytes) -> None:
        local = self._local(path)
        if not local.parent.is_dir():
            raise MobiGoError(f"parent directory does not exist: {path}")
        try:
            with local.open("wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except OSError as error:
            raise MobiGoError(f"cannot write {path}: {error}") from error

    def delete(self, path: str) -> None:
        local = self._local(path)
        try:
            local.rmdir() if local.is_dir() else local.unlink()
        except OSError as error:
            raise MobiGoError(f"cannot delete {path}: {error}") from error

    def mkdir(self, path: str) -> None:
        local = self._local(path)
        try:
            local.mkdir()
        except FileExistsError:
            if not local.is_dir():
                raise MobiGoError(f"a file already occupies {path}")
        except OSError as error:
            raise MobiGoError(f"cannot create {path}: {error}") from error


def _disk_info(device: str) -> dict:
    result = subprocess.run(["diskutil", "info", "-plist", device], capture_output=True)
    if result.returncode:
        return {}
    try:
        return plistlib.loads(result.stdout)
    except Exception:
        return {}


def _mac_discover() -> tuple[str, str, str | None]:
    matches: list[str] = []
    for block in sorted(glob.glob("/dev/disk[0-9]*")):
        if re.fullmatch(r"/dev/disk[0-9]+", block):
            info = _disk_info(block)
            if info.get("MediaName") == DEVICE_MODEL and info.get("BusProtocol") == "USB":
                matches.append(block)
    if len(matches) != 1:
        raise MobiGoError("connected MobiGo 2 was not found" if not matches else "multiple MobiGo devices found")
    block = matches[0]
    tree = plistlib.loads(subprocess.run(
        ["diskutil", "list", "-plist", block], capture_output=True, check=True
    ).stdout)
    parts = tree["AllDisksAndPartitions"][0]["Partitions"]
    partition_info = next((item for item in parts if item.get("Content") == "DOS_FAT_16_S"), None)
    if not partition_info:
        raise MobiGoError("MobiGo FAT16 transport partition was not found")
    partition = partition_info["DeviceIdentifier"]
    info = _disk_info("/dev/" + partition)
    return block, "/dev/r" + partition, info.get("MountPoint")


def _windows_discover() -> str:
    script = r"""
$disk = Get-CimInstance Win32_DiskDrive | Where-Object {
  $_.PNPDeviceID -match 'VID_0F88&PID_2D40' -or
  $_.Model -like 'VTECH USB-MSDC DISK A*'
} | Select-Object -First 1
if ($disk) {
  $parts = Get-CimAssociatedInstance $disk -Association Win32_DiskDriveToDiskPartition
  foreach ($part in $parts) {
    Get-CimAssociatedInstance $part -Association Win32_LogicalDiskToPartition |
      Select-Object -First 1 -ExpandProperty DeviceID
  }
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True
    )
    lines = result.stdout.strip().splitlines()
    if result.returncode or not lines:
        raise MobiGoError("connected MobiGo 2 was not found")
    return rf"\\.\{lines[-1].strip()}"


def _linux_discover() -> tuple[str, str | None]:
    result = subprocess.run(
        ["lsblk", "--json", "-o", "PATH,MODEL,TRAN,RM,FSTYPE,MOUNTPOINTS,TYPE"],
        capture_output=True, text=True, check=True
    )
    candidates = []
    for disk in json.loads(result.stdout).get("blockdevices", []):
        model = (disk.get("model") or "").strip()
        if disk.get("type") != "disk" or disk.get("tran") != "usb" or model != DEVICE_MODEL:
            continue
        for child in disk.get("children") or []:
            if (child.get("fstype") or "").lower() in {"vfat", "fat", "fat16"}:
                mounts = child.get("mountpoints") or []
                candidates.append((child["path"], next((item for item in mounts if item), None)))
    if len(candidates) != 1:
        raise MobiGoError("connected MobiGo 2 was not found" if not candidates else "multiple MobiGo devices found")
    return candidates[0]


class DeviceSession(AbstractContextManager):
    def __init__(self, device: str | None = None):
        self.requested = device
        self.backend = None
        self.remount: tuple[str, str | None] | None = None

    def __enter__(self):
        if self.requested:
            if self.requested.startswith("mount:"):
                return MountedMobiGoFS(self.requested[6:])
        if sys.platform == "darwin":
            block, raw, _mount = _mac_discover()
            subprocess.run(["diskutil", "unmountDisk", block], check=True, capture_output=True)
            self.remount = ("mac", block)
            self.backend = PosixBackend(raw)
        if os.name == "nt":
            self.backend = WindowsBackend(self.requested or _windows_discover())
        elif sys.platform != "darwin":
            raw, mount = (self.requested, None) if self.requested else _linux_discover()
            if mount:
                subprocess.run(["umount", raw], check=True, capture_output=True)
            self.remount = ("linux", raw if mount else None)
            self.backend = PosixBackend(raw)
        return MobiGoFS(MailboxTransport(self.backend))

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.backend is not None:
            self.backend.close()
        if self.remount and self.remount[0] == "mac" and self.remount[1]:
            subprocess.run(["diskutil", "mountDisk", self.remount[1]], capture_output=True)
        elif self.remount and self.remount[0] == "linux" and self.remount[1]:
            subprocess.run(["udisksctl", "mount", "-b", self.remount[1]], capture_output=True)
