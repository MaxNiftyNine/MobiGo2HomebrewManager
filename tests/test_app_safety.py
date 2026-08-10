from __future__ import annotations

import inspect
import unittest

from mobigo_homebrew_manager import app


class StartupSafetyTests(unittest.TestCase):
    def test_brick_warning_is_inline_in_advanced_tab(self):
        source = inspect.getsource(app.HomebrewManager._advanced_tab)
        warning = app.ADVANCED_BRICK_WARNING.lower()
        self.assertIn("brick", warning)
        self.assertIn("unable to boot", warning)
        self.assertIn("never unplug", warning)
        self.assertIn("verified backup", warning)
        self.assertIn("text=ADVANCED_BRICK_WARNING", source)
        self.assertIn('fg="#c00000"', source)
        self.assertLess(source.index("ADVANCED_BRICK_WARNING"), source.index("controls ="))

    def test_startup_has_no_brick_warning_popup(self):
        source = inspect.getsource(app.HomebrewManager.__init__)
        self.assertNotIn("confirm_startup_risk", source)
        self.assertNotIn("askokcancel", source)
        self.assertIn("self.after(250, self.refresh)", source)

    def test_declining_launcher_install_switches_to_advanced_only(self):
        source = inspect.getsource(app.HomebrewManager.refresh)
        self.assertIn("if install_now:", source)
        self.assertIn("self._advanced_only()", source)

        helper = inspect.getsource(app.HomebrewManager._advanced_only)
        self.assertIn("self.tabs.forget(self.home_tab)", helper)
        self.assertIn("self.tabs.select(self.advanced_tab)", helper)


if __name__ == "__main__":
    unittest.main()
