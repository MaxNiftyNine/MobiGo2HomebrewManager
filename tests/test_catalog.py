from pathlib import Path
import json
import tempfile
import unittest

from mobigo_homebrew_manager.catalog import CatalogEntry, decode, encode, load_hbi


class CatalogTests(unittest.TestCase):
    def test_round_trip_keeps_mba_suffix(self):
        entries = [
            CatalogEntry(r"A:\HB\Pong.MBA", "Pong", "A paddle game", "Max", 1),
            CatalogEntry(
                r"A:\HB\System.MBA", "System Menu", "Original menu", "VTech", 5
            ),
        ]
        encoded = encode(entries)
        self.assertEqual(encoded[:4], b"HB02")
        self.assertEqual(decode(encoded), entries)

    def test_starter_hbi_metadata_loads(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Pong.HBI"
            path.write_text(json.dumps({
                "schema": 1,
                "title": "Pong",
                "description": "A paddle game",
                "author": "Max",
                "icon": "game",
            }))
            self.assertEqual(
                load_hbi(path, fallback_title="fallback"),
                CatalogEntry("unused", "Pong", "A paddle game", "Max", 1),
            )

    def test_rejects_more_than_launcher_capacity(self):
        entries = [CatalogEntry(fr"A:\HB\A{i}.MBA", f"A{i}.MBA") for i in range(17)]
        with self.assertRaises(ValueError):
            encode(entries)


if __name__ == "__main__":
    unittest.main()
