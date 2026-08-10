from __future__ import annotations

from pathlib import Path, PurePosixPath
import struct
import tempfile
import unittest

from mobigo_homebrew_manager.catalog import CatalogEntry, decode
from mobigo_homebrew_manager.service import (
    ManagerError,
    RemoteEntry,
    add_homebrew,
    delete_homebrew,
    install_launcher,
    install_or_update_launcher,
    rename_file,
    rebuild_catalog,
    set_developer_mode,
    uninstall_homebrew,
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
        self.files = {"/USENG/MM.MBA": mba("G1", 0x11)}
        self.directories = {"/", "/USENG", "/ETC"}
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

    def rmdir(self, path):
        self.log.append(("rmdir", path))
        prefix = path.rstrip("/") + "/"
        if any(item.startswith(prefix) for item in self.files):
            raise OSError("directory still has files")
        if any(item != path and item.startswith(prefix) for item in self.directories):
            raise OSError("directory still has directories")
        self.directories.remove(path)

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
        original = fs.files["/USENG/MM.MBA"]
        with tempfile.TemporaryDirectory() as temporary:
            result = install_launcher(fs, launcher, Path(temporary))
            self.assertEqual(result.local_backup.read_bytes(), original)
        self.assertEqual(fs.files["/HB/System.MBA"], original)
        self.assertEqual(fs.files["/USENG/MM.MBA"], launcher)
        catalog = decode(fs.files["/HB/INDEX.HB"])
        self.assertEqual(catalog[0].title, "System Menu")
        self.assertEqual(catalog[0].icon, 5)
        system_write = fs.log.index(("write", "/USENG/MM.MBA"))
        self.assertLess(fs.log.index(("read", "/HB/System.MBA")), system_write)
        self.assertLess(fs.log.index(("read", "/HB/INDEX.HB")), system_write)

    def test_install_never_writes_system_when_remote_backup_fails(self):
        fs = FakeFS()
        fs.corrupt_path = "/HB/System.MBA"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "backup verification"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertNotIn(("write", "/USENG/MM.MBA"), fs.log)

    def test_launcher_update_preserves_original_system_recovery(self):
        fs = FakeFS()
        original = fs.files["/USENG/MM.MBA"]
        old_launcher = mba("SY", 0x22)
        new_launcher = mba("SY", 0x33)
        fs.files["/USENG/MM.MBA"] = old_launcher
        fs.mkdir("/HB")
        fs.files["/HB/System.MBA"] = original
        with tempfile.TemporaryDirectory() as temporary:
            result = install_or_update_launcher(fs, new_launcher, Path(temporary))
            self.assertEqual(result.local_backup.read_bytes(), original)
        self.assertEqual(fs.files["/HB/System.MBA"], original)
        self.assertEqual(fs.files["/USENG/MM.MBA"], new_launcher)

    def test_rebuild_migrates_legacy_filename_cards_to_metadata(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        fs.files["/HB/Pong.MBA"] = mba("G1", 0x33)
        path = b"A:\\HB\\Pong.MBA".ljust(42, b"\0")
        label = b"Pong.MBA".ljust(20, b"\0")
        fs.files["/HB/INDEX.HB"] = (
            b"HB01" + struct.pack("<HH", 1, 64) + path + label + b"\0\0"
        )
        entries = rebuild_catalog(fs)
        self.assertEqual(fs.files["/HB/INDEX.HB"][:4], b"HB02")
        self.assertEqual((entries[0].title, entries[0].icon), ("Pong", 1))

    def test_install_never_writes_system_when_hb_directory_is_not_published(self):
        fs = FakeFS()
        fs.mkdir = lambda path: fs.log.append(("mkdir", path))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "did not publish /HB"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertNotIn(("write", "/USENG/MM.MBA"), fs.log)

    def test_failed_launcher_write_restores_original(self):
        fs = FakeFS()
        original = fs.files["/USENG/MM.MBA"]
        writes = 0
        real_write = fs.write_file
        def fail_once(path, data):
            nonlocal writes
            if path == "/USENG/MM.MBA":
                writes += 1
                if writes == 1:
                    fs.files[path] = b"partial"
                    raise OSError("disconnect")
            return real_write(path, data)
        fs.write_file = fail_once
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ManagerError, "original MM.MBA was restored"):
                install_launcher(fs, mba("SY", 0x22), Path(temporary))
        self.assertEqual(fs.files["/USENG/MM.MBA"], original)

    def test_add_delete_rename_and_dmode_verify(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        app = mba("G1", 0x33)
        add_homebrew(
            fs,
            "Pong.MBA",
            app,
            metadata=CatalogEntry(
                "unused", "Pong", "Classic paddle game", "Max", 1
            ),
        )
        self.assertEqual(fs.files["/HB/Pong.MBA"], app)
        self.assertEqual(
            decode(fs.files["/HB/INDEX.HB"])[0].description,
            "Classic paddle game",
        )
        rename_file(fs, "/HB/Pong.MBA", "/HB/Pong2.MBA")
        self.assertNotIn("/HB/Pong.MBA", fs.files)
        self.assertEqual(fs.files["/HB/Pong2.MBA"], app)
        renamed = decode(fs.files["/HB/INDEX.HB"])[0]
        self.assertEqual((renamed.title, renamed.icon), ("Pong", 1))
        delete_homebrew(fs, "Pong2.MBA")
        self.assertNotIn("/HB/Pong2.MBA", fs.files)
        set_developer_mode(fs, True)
        self.assertEqual(fs.files["/ETC/DMODE"], b"")
        set_developer_mode(fs, False)
        self.assertNotIn("/ETC/DMODE", fs.files)

    def test_add_homebrew_accepts_unrecognized_mba_metadata(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        custom = bytearray(mba("G1", 0x44))
        struct.pack_into("<III", custom, 0x10, 0x1F, 0x22D88B, 0x224800)
        app = bytes(custom)
        add_homebrew(fs, "Custom.MBA", app)
        self.assertEqual(fs.files["/HB/Custom.MBA"], app)

    def test_recovery_copy_requires_full_uninstall(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        fs.files["/HB/System.MBA"] = mba("SY", 0x11)
        with self.assertRaisesRegex(ManagerError, "Delete all homebrew and exit"):
            delete_homebrew(fs, "System.MBA")

    def test_uninstall_restores_system_then_removes_hb_tree(self):
        fs = FakeFS()
        original = fs.files["/USENG/MM.MBA"]
        launcher = mba("SY", 0x22)
        fs.files["/USENG/MM.MBA"] = launcher
        fs.mkdir("/HB")
        fs.mkdir("/HB/SUB")
        fs.files["/HB/System.MBA"] = original
        fs.files["/HB/Pong.MBA"] = mba("G1", 0x33)
        fs.files["/HB/SUB/NOTE.DAT"] = b"safe"
        with tempfile.TemporaryDirectory() as temporary:
            result = uninstall_homebrew(fs, Path(temporary))
            self.assertEqual(result.local_backup.read_bytes(), original)
        self.assertEqual(fs.files["/USENG/MM.MBA"], original)
        self.assertNotIn("/HB", fs.directories)
        self.assertFalse(any(path.startswith("/HB/") for path in fs.files))
        system_write = fs.log.index(("write", "/USENG/MM.MBA"))
        first_delete = next(i for i, item in enumerate(fs.log) if item[0] == "delete")
        self.assertLess(system_write, first_delete)

    def test_names_longer_than_retail_directory_limit_are_rejected(self):
        fs = FakeFS()
        fs.mkdir("/HB")
        with self.assertRaisesRegex(ManagerError, "at most 12"):
            add_homebrew(fs, "ThirteenX.MBA", mba("SY", 0x22))

    def test_retail_dmode_close_failure_reports_reboot(self):
        fs = FakeFS()
        def retail_empty_close(path, data):
            fs.files[path] = bytes(data)
            raise OSError("closing file failed (device status -1)")
        fs.write_file = retail_empty_close
        self.assertTrue(set_developer_mode(fs, True))
        self.assertEqual(fs.files["/ETC/DMODE"], b"")


if __name__ == "__main__":
    unittest.main()
