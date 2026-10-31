"""Browser preferences persist independently of browsing data."""

import tempfile
import unittest
from pathlib import Path

from mini_browser.storage import Library


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.sqlite3"
        self.library = Library(self.path)

    def test_home_button_defaults_on_and_remembers_both_choices(self):
        self.assertTrue(self.library.show_home_button())
        for visible in (False, True):
            self.library.set_show_home_button(visible)
            self.assertEqual(Library(self.path).show_home_button(), visible)
        self.assertEqual(self.library.search("history"), [])
        self.assertEqual(self.library.recent_apps(), [])

    def test_existing_library_gets_default_without_changing_history(self):
        self.library.record_visit("https://example.org/", "Synthetic")
        with self.library.connect() as db:
            db.execute("DROP TABLE preferences")
        upgraded = Library(self.path)
        self.assertTrue(upgraded.show_home_button())
        self.assertEqual(upgraded.search("history")[0]["title"], "Synthetic")
