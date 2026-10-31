"""Saved Home apps are independent of browsing history and survive restarts."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_browser.storage import Library, bookmark_app_url, home_app_url


class HomeAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "library.sqlite3"
        self.library = Library(self.path)

    def test_saved_apps_persist_without_visits_or_bookmarks(self):
        saved = self.library.add_home_app("https://example.org/docs?q=1#start", "Docs 中文 <app>")
        self.assertEqual(saved["title"], "Docs 中文 <app>")
        self.assertEqual(saved["url"], "https://example.org/docs?q=1#start")
        self.assertEqual(saved["site"], "https://example.org")
        self.assertEqual(Library(self.path).home_apps(), [saved])
        self.assertEqual(self.library.search("history"), [])
        self.assertEqual(self.library.search("bookmarks"), [])
        self.assertEqual(self.library.recent_apps(), [])

    def test_repeated_add_updates_name_preserves_order_and_separate_paths(self):
        with patch("mini_browser.storage.time.time", return_value=10):
            first = self.library.add_home_app("https://EXAMPLE.org:443", "First")
        self.library.add_home_app("https://example.org/docs", "Docs")
        repeated = self.library.add_home_app("https://example.org/", "Renamed")
        self.assertEqual(repeated["created_at"], first["created_at"])
        self.assertEqual(self.library.add_home_app("https://example.org")["title"], "Renamed")
        self.assertEqual([app["title"] for app in self.library.home_apps()], ["Renamed", "Docs"])

    def test_default_title_and_url_normalization(self):
        self.assertEqual(self.library.add_home_app("http://localhost:8000")["title"], "localhost")
        for address, expected in (
            ("  HTTPS://EXAMPLE.ORG:443  ", "https://example.org/"),
            ("http://example.org:80/x", "http://example.org/x"),
            ("http://[::1]:8000/a?b=1#c", "http://[::1]:8000/a?b=1#c"),
        ):
            with self.subTest(address=address):
                self.assertEqual(home_app_url(address), expected)

    def test_reject_invalid_addresses_and_names_without_writes(self):
        for address in (
            "",
            "example.org",
            "search words",
            "javascript:alert(1)",
            "file:///tmp/x",
            "https://example.org:0",
            "https://example.org:bad",
            "https://user:secret@example.org",
            "https://example.org/with space",
            "https://example.org/\0x",
            "http://[broken",
        ):
            with self.subTest(kind="url"), self.assertRaises(ValueError):
                self.library.add_home_app(address)
        for title in ("", "   ", "x" * 201, "line\nbreak", "nul\0value"):
            with self.subTest(kind="title"), self.assertRaises(ValueError):
                self.library.add_home_app("https://example.org", title)
        self.assertEqual(self.library.home_apps(), [])

    def test_recent_exclusions_do_not_consume_the_recent_limit(self):
        for index in range(9):
            self.library.record_visit(f"https://site{index}.example/page", f"Site {index}")
        excluded = {f"https://site{index}.example" for index in (6, 7, 8)}
        recent = self.library.recent_apps(exclude_sites=excluded)
        self.assertEqual(len(recent), 6)
        self.assertTrue(all(app["site"] not in excluded for app in recent))
        self.assertEqual(len(self.library.recent_apps()), 6)

    def test_remove_only_affects_saved_tile_and_is_idempotent(self):
        self.library.record_visit("https://example.org/", "Visited")
        self.library.add_bookmark("https://example.org/", "Bookmark")
        self.library.add_home_app("https://example.org", "Saved")
        before = (
            self.library.search("history"),
            self.library.search("bookmarks"),
            self.library.recent_apps(),
        )
        self.assertTrue(self.library.remove_home_app("https://EXAMPLE.org:443"))
        self.assertFalse(self.library.remove_home_app("https://example.org/"))
        self.assertEqual(self.library.home_apps(), [])
        self.assertEqual(
            before,
            (
                self.library.search("history"),
                self.library.search("bookmarks"),
                self.library.recent_apps(),
            ),
        )

    def test_existing_library_upgrades_without_changing_records(self):
        self.library.record_visit("https://example.org/", "Old visit")
        before = self.library.search("history")
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE home_apps")
        upgraded = Library(self.path)
        self.assertEqual(upgraded.home_apps(), [])
        self.assertEqual(upgraded.search("history"), before)
        upgraded.add_home_app("https://example.org", "Saved after upgrade")
        self.assertEqual(upgraded.search("history"), before)

    def test_uninstall_presets_and_imported_sites_persists_without_losing_data(self):
        bookmarks = [("https://example.org/docs", "Docs", "Imported", 1)]
        self.library.import_records(bookmarks, [])
        self.library.record_visit("https://example.org/docs", "Docs")
        before = (self.library.search("bookmarks"), self.library.search("history"))
        for url in ("https://www.youtube.com/", "https://example.org/"):
            self.library.uninstall_web_app(url)
            self.library.uninstall_web_app(url)
        reopened = Library(self.path)
        reopened.import_records(bookmarks, [])
        self.assertEqual(
            [app["url"] for app in reopened.bookmarked_apps()], ["https://www.bilibili.com/"]
        )
        self.assertEqual(before, (reopened.search("bookmarks"), reopened.search("history")))
        reopened.record_visit("https://example.org/another", "Another visit")
        self.assertEqual(len(reopened.bookmarked_apps()), 1)

    def test_uninstall_saved_url_keeps_other_apps_and_hides_bookmark_fallback(self):
        self.library.add_bookmark_app("https://example.org/")
        self.library.add_home_app("https://example.org/first", "First")
        second = self.library.add_home_app("https://example.org/second", "Second")
        self.library.add_bookmark_app("http://example.org/")
        self.library.add_bookmark_app("https://sub.example.org/")
        self.library.uninstall_web_app("https://EXAMPLE.org:443/first")
        self.assertEqual(Library(self.path).home_apps(), [second])
        roots = {app["url"] for app in self.library.bookmarked_apps()}
        self.assertNotIn("https://example.org/", roots)
        self.assertIn("http://example.org/", roots)
        self.assertIn("https://sub.example.org/", roots)

    def test_explicit_bookmark_or_saved_app_restores_uninstalled_tile(self):
        self.library.uninstall_web_app("https://www.youtube.com/")
        self.library.add_bookmark_app("https://www.youtube.com/watch?v=fixture")
        self.assertIn(
            "https://www.youtube.com/", [app["url"] for app in self.library.bookmarked_apps()]
        )
        self.library.uninstall_web_app("https://example.org/docs")
        restored = self.library.add_home_app("https://example.org/docs", "Restored")
        self.assertEqual(Library(self.path).home_apps(), [restored])

    def test_failed_uninstall_rolls_back_saved_tile_removal(self):
        saved = self.library.add_home_app("https://example.org/docs", "Saved")
        with self.library.connect() as database:
            database.execute("""CREATE TRIGGER fail_uninstall
                BEFORE INSERT ON hidden_bookmark_apps
                BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        with self.assertRaises(sqlite3.Error):
            self.library.uninstall_web_app(saved["url"])
        self.assertEqual(self.library.home_apps(), [saved])

    def test_existing_library_gains_uninstall_state_without_changing_apps(self):
        saved = self.library.add_home_app("https://example.org/docs", "Saved")
        with self.library.connect() as database:
            database.execute("DROP TABLE hidden_bookmark_apps")
        upgraded = Library(self.path)
        self.assertEqual(upgraded.home_apps(), [saved])
        self.assertEqual(len(upgraded.bookmarked_apps()), 2)
        upgraded.uninstall_web_app(saved["url"])
        self.assertEqual(upgraded.home_apps(), [])

    def test_bookmarked_apps_open_site_roots_and_preserve_subdomains(self):
        for address, expected in (
            ("https://APC.example.com:443/sdacsd/sdca?q=1#part", "https://apc.example.com/"),
            ("http://example.org:8080/page", "http://example.org:8080/"),
            ("http://[::1]:8000/page", "http://[::1]:8000/"),
        ):
            with self.subTest(address=address):
                self.assertEqual(bookmark_app_url(address), expected)
                self.library.add_bookmark_app(address)
        self.library.add_bookmark_app("https://apc.example.com/another/path")
        bookmarks = self.library.search("bookmarks", limit=None)
        self.assertEqual(len(bookmarks), 3)
        self.assertIn(
            ("https://apc.example.com/", "apc.example.com"),
            [(row["url"], row["title"]) for row in bookmarks],
        )
        self.assertEqual(self.library.search("history"), [])

    def test_presets_and_imported_bookmarks_are_grouped_without_rewriting_sources(self):
        bookmarks = [
            ("https://apc.example.com/first", "First page", "Imported", 1),
            ("https://apc.example.com/second?q=1", "Second page", "Imported", 2),
            ("https://other.example.com/page", "Other page", "Imported", 3),
            ("https://www.youtube.com/watch?v=fixture", "A video", "Imported", 4),
        ]
        self.library.import_records(bookmarks, [])
        before = self.library.search("bookmarks", limit=None)
        apps = self.library.bookmarked_apps()
        self.assertEqual(
            [(app["url"], app["title"]) for app in apps],
            [
                ("https://www.youtube.com/", "YouTube"),
                ("https://www.bilibili.com/", "Bilibili"),
                ("https://other.example.com/", "other.example.com"),
                ("https://apc.example.com/", "apc.example.com"),
            ],
        )
        self.assertEqual(Library(self.path).bookmarked_apps(), apps)
        self.assertEqual(self.library.search("bookmarks", limit=None), before)

    def test_complete_history_and_bookmarked_apps_exceed_dialog_limit(self):
        self.library.import_records(
            [(f"https://site{i}.example/page", str(i), "", i) for i in range(250)],
            [(f"https://example.org/page/{i}", str(i), i, 1) for i in range(350)],
        )
        self.assertEqual(len(self.library.search("history")), 200)
        history = self.library.search("history", limit=None)
        self.assertEqual(len(history), 350)
        self.assertEqual(history[0]["url"], "https://example.org/page/349")
        self.assertEqual(history[-1]["url"], "https://example.org/page/0")
        self.assertEqual(len(self.library.bookmarked_apps()), 252)

    def test_invalid_bookmark_app_never_writes(self):
        for address in ("file:///tmp/x", "https://user:secret@example.org/path", "not a URL"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.library.add_bookmark_app(address)
        self.assertEqual(self.library.search("bookmarks"), [])


if __name__ == "__main__":
    unittest.main()
