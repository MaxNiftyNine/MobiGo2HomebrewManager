import unittest

from mobigo_homebrew_manager.catalog import CatalogEntry, decode, encode


class CatalogTests(unittest.TestCase):
    def test_round_trip_keeps_mba_suffix(self):
        entries = [
            CatalogEntry(r"A:\HB\Pong.MBA", "Pong.MBA"),
            CatalogEntry(r"A:\HB\SystemMenu.MBA", "SystemMenu.MBA"),
        ]
        self.assertEqual(decode(encode(entries)), entries)

    def test_rejects_more_than_launcher_capacity(self):
        entries = [CatalogEntry(fr"A:\HB\A{i}.MBA", f"A{i}.MBA") for i in range(17)]
        with self.assertRaises(ValueError):
            encode(entries)


if __name__ == "__main__":
    unittest.main()
