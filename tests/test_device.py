import struct
import tempfile
import unittest

from mobigo_homebrew_manager.device import BLOCK_SIZE, MobiGoError, MobiGoFS, MountedMobiGoFS
from mobigo_homebrew_manager.service import RemoteEntry


def response(status=0):
    data = bytearray(BLOCK_SIZE)
    struct.pack_into("<h", data, 0, status)
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

    def test_parent_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MobiGoError, "parent directory"):
                MountedMobiGoFS(directory).write_file("/HB/TEST.MBA", b"test")


if __name__ == "__main__":
    unittest.main()
