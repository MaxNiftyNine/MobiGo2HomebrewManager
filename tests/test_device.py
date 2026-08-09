import struct
import tempfile
import unittest

from mobigo_homebrew_manager.device import BLOCK_SIZE, MobiGoError, MobiGoFS, MountedMobiGoFS
from mobigo_homebrew_manager.service import RemoteEntry


def response(status=0):
    data = bytearray(BLOCK_SIZE)
    struct.pack_into("<h", data, 0, status)
    return bytes(data)


def directory_page(*entries):
    data = bytearray(BLOCK_SIZE)
    for index, (name, size, kind) in enumerate(entries):
        offset = index * 28
        struct.pack_into("<h", data, offset, index)
        encoded = name.encode("ascii")
        data[offset + 4 : offset + 4 + len(encoded)] = encoded
        struct.pack_into("<H", data, offset + 18, kind)
        struct.pack_into("<I", data, offset + 24, size)
    struct.pack_into("<h", data, len(entries) * 28, -1)
    return bytes(data)


class Transport:
    def __init__(self, reads):
        self.reads = list(reads)
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, size):
        data = self.reads.pop(0)
        assert len(data) == size, (len(data), size)
        return data


class ProtocolTests(unittest.TestCase):
    def test_read_handle_uses_command_three_and_trims_padding(self):
        payload = bytes((index & 0xFF) for index in range(BLOCK_SIZE))
        transport = Transport([payload, response()])
        result = MobiGoFS(transport).read_handle(7, 500)
        self.assertEqual(result, payload[:500])
        request = transport.writes[0]
        self.assertEqual(struct.unpack_from("<I", request, 0)[0], 3)
        self.assertEqual(struct.unpack_from("<H", request, 4)[0], 7)
        self.assertEqual(struct.unpack_from("<I", request, 8)[0], 512)

    def test_mkdir_uses_guarded_path_command(self):
        transport = Transport([response(ord("A")), response()])
        MobiGoFS(transport).mkdir("/HB")
        request = transport.writes[1]
        self.assertEqual(struct.unpack_from("<I", request, 0)[0], 0x0A)
        self.assertEqual(request[4:9], b"A:\\HB")

    def test_rmdir_uses_retail_command_zero_b(self):
        transport = Transport([response(ord("A")), response()])
        MobiGoFS(transport).rmdir("/HB")
        request = transport.writes[1]
        self.assertEqual(struct.unpack_from("<I", request, 0)[0], 0x0B)
        self.assertEqual(request[4:9], b"A:\\HB")

    def test_retail_zero_stat_does_not_make_absent_file_exist(self):
        fs = MobiGoFS(Transport([response(0), directory_page()]))
        fs._drive = "A"
        self.assertIsNone(fs.stat_size("/HB/DOESNOT.MBA"))

    def test_retail_directory_is_found_through_parent_listing(self):
        fs = MobiGoFS(Transport([response(0), directory_page(("HB", 0, 2))]))
        fs._drive = "A"
        self.assertEqual(fs.stat_size("/HB"), 0)

    def test_existing_empty_file_uses_unambiguous_file_type(self):
        fs = MobiGoFS(Transport([response(1), response(0)]))
        fs._drive = "A"
        self.assertEqual(fs.stat_size("/ETC/DMODE"), 0)

    def test_odd_sized_write_is_rejected_before_device_io(self):
        transport = Transport([])
        with self.assertRaisesRegex(MobiGoError, "even byte length"):
            MobiGoFS(transport).write_file("/HB/ODD.DAT", b"odd")
        self.assertEqual(transport.writes, [])


class MountedFilesystemTests(unittest.TestCase):
    def test_directory_and_empty_file_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            fs = MountedMobiGoFS(directory)
            fs.mkdir("/HB")
            fs.write_file("A:\\HB\\DMODE", b"")
            self.assertEqual(fs.read_file("/HB/DMODE"), b"")
            self.assertEqual(fs.stat_size("/HB/DMODE"), 0)
            self.assertEqual(
                fs.listdir("/HB"),
                [RemoteEntry("DMODE", 0, False)],
            )
            fs.delete("/HB/DMODE")
            self.assertIsNone(fs.stat_size("/HB/DMODE"))
            fs.rmdir("/HB")
            self.assertIsNone(fs.stat_size("/HB"))

    def test_parent_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MobiGoError, "parent directory"):
                MountedMobiGoFS(directory).write_file("/HB/TEST.MBA", b"test")


if __name__ == "__main__":
    unittest.main()
