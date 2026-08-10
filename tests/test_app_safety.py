from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from mobigo_homebrew_manager import app


class StartupSafetyTests(unittest.TestCase):
    def test_warning_is_explicit_and_cancel_is_default(self):
        parent = object()
        with patch.object(app.messagebox, "askokcancel", return_value=False) as ask:
            self.assertFalse(app.confirm_startup_risk(parent))

        ask.assert_called_once_with(
            app.BRICK_WARNING_TITLE,
            app.BRICK_WARNING_MESSAGE,
            parent=parent,
            icon=app.messagebox.WARNING,
            default=app.messagebox.CANCEL,
        )
        warning = (app.BRICK_WARNING_TITLE + " " + app.BRICK_WARNING_MESSAGE).lower()
        self.assertIn("brick", warning)
        self.assertIn("unable to boot", warning)
        self.assertIn("never unplug", warning)
        self.assertIn("verified backup", warning)

    def test_warning_precedes_automatic_device_refresh(self):
        source = inspect.getsource(app.HomebrewManager.__init__)
        self.assertLess(
            source.index("confirm_startup_risk"),
            source.index("self.after(250, self.refresh)"),
        )
        self.assertIn("self.destroy()", source)


if __name__ == "__main__":
    unittest.main()
