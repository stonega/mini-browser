import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from icon_helper import png

from mini_browser.launcher import app_catalog
from mini_browser.site_icons import SiteIconCache, save_custom_icon
from mini_browser.storage import Library


class SiteIconTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = SiteIconCache(self.root / "cache")
        self.library = Library(self.root / "data/library.sqlite3")

    def test_cached_icons_share_origin_persist_and_change_filename_on_update(self):
        url = "https://example.org/dashboard"
        self.assertIsNone(self.cache.path_for(url))
        self.assertFalse(self.cache.directory.exists())
        self.assertTrue(self.cache.save(url, png()))
        first = self.cache.path_for("https://example.org/other")
        self.assertEqual(first.read_bytes(), png())
        self.assertIsNone(self.cache.path_for("http://example.org"))
        self.assertIsNone(self.cache.path_for("https://sub.example.org"))
        self.assertFalse(self.cache.save(url, png()))
        self.assertTrue(self.cache.save(url, png((80, 90, 200))))
        updated = SiteIconCache(self.cache.directory).path_for(url)
        self.assertNotEqual(first, updated)
        self.assertFalse(first.exists())
        self.assertEqual(updated.stat().st_mode & 0o777, 0o600)

    def test_invalid_data_and_failed_index_write_preserve_previous_icon(self):
        url = "https://example.org"
        self.cache.save(url, png())
        previous = self.cache.path_for(url)
        for data in (b"not an image", png(size=257), png() + b"x" * 1048576):
            with self.assertRaises(ValueError):
                self.cache.save(url, data)
        real_replace = os.replace

        def replace(source, destination):
            if str(destination).endswith(".json"):
                raise OSError("synthetic failed write")
            return real_replace(source, destination)

        with patch("mini_browser.site_icons.os.replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.cache.save(url, png((200, 30, 30)))
        self.assertEqual(self.cache.path_for(url), previous)
        index = next(self.cache.directory.glob("*.json"))
        index.write_text(json.dumps("../../private.png"))
        self.assertIsNone(self.cache.path_for(url))

    def test_custom_icon_overrides_favicon_and_survives_path_and_title_edits(self):
        saved = self.library.add_home_app("https://sss.example.org", "Original")
        self.cache.save(saved["url"], png())
        custom = save_custom_icon(self.library, png((200, 30, 30)))
        updated = self.library.edit_web_app(
            saved["url"], "Dashboard", "/dashboard?view=home#top", icon=custom
        )
        self.assertEqual(updated["url"], "https://sss.example.org/dashboard?view=home#top")
        record = next(
            record
            for record in app_catalog(self.library, self.cache)
            if record["title"] == "Dashboard"
        )
        self.assertEqual(record["icon"], updated["icon"])
        self.assertEqual(Path(record["icon"]).read_bytes(), png((200, 30, 30)))
        updated = Library(self.library.path).edit_web_app(updated["url"], "Renamed", "next")
        self.assertTrue(updated["icon"].endswith(custom))
        updated = self.library.edit_web_app(updated["url"], "Renamed", "/next", icon="")
        self.assertNotIn("icon", updated)
        record = next(
            record
            for record in app_catalog(self.library, self.cache)
            if record["title"] == "Renamed"
        )
        self.assertEqual(record["icon"], str(self.cache.path_for(saved["url"])))

    def test_preset_edit_preserves_bookmarks_and_uninstall_suppression(self):
        self.library.add_bookmark("https://www.youtube.com/watch?v=fixture", "Source bookmark")
        before = self.library.search("bookmarks")
        updated = self.library.edit_web_app(
            "https://www.youtube.com/", "Subscriptions", "/feed/subscriptions"
        )
        self.assertEqual(
            [
                app["url"]
                for app in self.library.web_apps()
                if app["site"] == "https://www.youtube.com"
            ],
            [updated["url"]],
        )
        self.assertEqual(Library(self.library.path).web_apps(), self.library.web_apps())
        self.library.uninstall_web_app(updated["url"])
        self.assertFalse(
            any(app["site"] == "https://www.youtube.com" for app in self.library.web_apps())
        )
        self.assertEqual(self.library.search("bookmarks"), before)

    def test_edit_rejects_origin_changes_collisions_and_removed_apps_atomically(self):
        saved = self.library.add_home_app("https://sss.example.org", "First")
        self.library.add_home_app("https://sss.example.org/existing", "Other")
        before = self.library.web_apps()
        for path in (
            "https://evil.example/",
            "//evil.example/",
            "\\evil.example",
            "/bad\npath",
            "/existing",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.library.edit_web_app(saved["url"], "Changed", path)
            self.assertEqual(self.library.web_apps(), before)
        self.library.uninstall_web_app(saved["url"])
        with self.assertRaises(ValueError):
            self.library.edit_web_app(saved["url"], "Changed", "/new")
