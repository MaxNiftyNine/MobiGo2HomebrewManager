from __future__ import annotations

from pathlib import Path, PurePosixPath
import struct
import tempfile
import unittest

from mobigo_homebrew_manager.catalog import decode
from mobigo_homebrew_manager.service import (
    ManagerError,
    RemoteEntry,
    add_homebrew,
    delete_homebrew,
    install_launcher,
    rename_file,
    set_developer_mode,
)


def mba(role: str, fill: int) -> bytes:
    if role == "SY":
        size, field, compat, entry = 0x174000, 0x5387A, 0x0F3E60, 0x0DFC1D
    else:
        size, field, compat, entry = 0x214000, 0x3BC0B, 0x0F3E5C, 0x0E1A55
    data = bytearray([fill] * size)
    data[:8] = b"bM_gbMQa"
    struct.pack_into("<5I", data, 8, size // 2, field, compat, entry, 0x0C8800)
    return bytes(data)


class FakeFS:
    def __init__(self):
        self.files = {"/BUNDLE/SY/135804SY.MBA": mba("SY", 0x11)}
        self.directories = {"/", "/BUNDLE", "/BUNDLE/SY", "/ETC"}
        self.log = []
        self.corrupt_path = None
        self.fail_path = None

    def listdir(self, path):
        self.log.append(("list", path))
        prefix = path.rstrip("/") + "/"
        output = []
        for directory in self.directories:
            if directory.startswith(prefix) and "/" not in directory[len(prefix):]:
                output.append(RemoteEntry(PurePosixPath(directory).name, 0, True))
        for filename, data in self.files.items():
            if filename.startswith(prefix) and "/" not in filename[len(prefix):]:
                output.append(RemoteEntry(PurePosixPath(filename).name, len(data), False))
        return output

    def read_file(self, path):
        self.log.append(("read", path))
        data = self.files[path]
        return data + b"broken" if path == self.corrupt_path else data

    def write_file(self, path, data):
        self.log.append(("write", path))
        if path == self.fail_path:
            raise OSError("injected write failure")
        parent = str(PurePosixPath(path).parent)
        if parent not in self.directories:
            raise OSError("missing directory")
        self.files[path] = bytes(data)

    def delete(self, path):
        self.log.append(("delete", path))
        del self.files[path]

    def mkdir(self, path):
        self.log.append(("mkdir", path))
        self.directories.add(path)

    def stat_size(self, path):
        if path in self.files:
            return len(self.files[path])
        if path in self.directories:
            return 0
        return None


class ServiceTests(unittest.TestCase):
    def test_install_backs_up_twice_before_system_write(self):
        fs = FakeFS()
        launcher = mba("SY", 0x22)
        original = fs.files["/BUNDLE/SY/135804SY.MBA"]
        with tempfile.TemporaryDirectory() as temporary:
            result = install_launcher(fs, launcher, Path(temporary))
            self.assertEqual(result.local_backup.read_bytes(), original)
        self.assertEqual(fs.files["/HB/SystemMenu.MBA"], original)
        self.assertEqual(fs.files["/BUNDLE/SY/135804SY.MBA"], launcher)
        catalog = decode(fs.files["/HB/INDEX.HB"])
        self.assertEqual(catalog[0].label, "SystemMenu.MBA")
        system_write = fs.log.index(("write", "/BUNDLE/SY/135804SY.MBA"))
        self.assertLess(fs.log.index(("read", "/HB/SystemMenu.MBA")), system_write)
        self.assertLess(fs.log.index(("read", "/HB/INDEX.HB")), system_write)

    def test_install_never_writes_system_when_remote_backup_fails(self):
        fs = FakeFS()
        fs.corrupt_path = "/HB/SystemMenu.MBA"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "backup verification"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertNotIn(("write", "/BUNDLE/SY/135804SY.MBA"), fs.log)

    def test_install_never_writes_system_when_hb_directory_is_not_published(self):
        fs = FakeFS()
        fs.mkdir = lambda path: fs.log.append(("mkdir", path))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "did not publish /HB"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertNotIn(("write", "/BUNDLE/SY/135804SY.MBA"), fs.log)

    def test_failed_launcher_write_restores_original(self):
        fs = FakeFS()
        original = fs.files["/BUNDLE/SY/135804SY.MBA"]
        writes = 0
        real_write = fs.write_file
        def fail_once(path, data):
            nonlocal writes
            if path == "/BUNDLE/SY/135804SY.MBA":
                writes += 1
                if writes == 1:
                    fs.files[path] = b"partial"
                    raise OSError("disconnect")
            return real_write(path, data)
        fs.write_file = fail_once
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "original SY was restored"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertEqual(fs.files["/BUNDLE/SY/135804SY.MBA"], original)

    def test_add_delete_rename_and_dmode_verify(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        app = mba("G1", 0x33)
        add_homebrew(fs, "Pong.MBA", app)
        self.assertEqual(fs.files["/HB/Pong.MBA"], app)
        rename_file(fs, "/HB/Pong.MBA", "/HB/Pong2.MBA")
        self.assertNotIn("/HB/Pong.MBA", fs.files)
        self.assertEqual(fs.files["/HB/Pong2.MBA"], app)
        delete_homebrew(fs, "Pong2.MBA")
        self.assertNotIn("/HB/Pong2.MBA", fs.files)
        set_developer_mode(fs, True)
        self.assertEqual(fs.files["/ETC/DMODE"], b"")
        set_developer_mode(fs, False)
        self.assertNotIn("/ETC/DMODE", fs.files)

    def test_recovery_copy_is_protected(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        fs.files["/HB/SystemMenu.MBA"] = mba("SY", 0x11)
        with self.assertRaisesRegex(ManagerError, "protected"):
            delete_homebrew(fs, "SystemMenu.MBA")


if __name__ == "__main__":
    unittest.main()
