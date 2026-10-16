import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from mini_browser.chrome import (
    CHROME_EPOCH_OFFSET,
    chrome_time,
    discover_profiles,
    import_html,
    import_profile,
)
from mini_browser.storage import Library


class ChromeImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "config/google-chrome/Default"
        self.profile.mkdir(parents=True)
        self.library = Library(self.root / "mini/library.sqlite3")
        self.timestamp = str((CHROME_EPOCH_OFFSET + 1_700_000_000) * 1_000_000)
        bookmarks = {
            "roots": {
                "bookmark_bar": {
                    "type": "folder",
                    "name": "Bookmarks bar",
                    "children": [
                        {
                            "type": "folder",
                            "name": "Work",
                            "children": [
                                {
                                    "type": "url",
                                    "name": "GNOME",
                                    "url": "https://gnome.org/",
                                    "date_added": self.timestamp,
                                },
                            ],
                        },
                        {"type": "url", "name": "Bad", "url": "javascript:alert(1)"},
                    ],
                },
            },
        }
        (self.profile / "Bookmarks").write_text(json.dumps(bookmarks), encoding="utf-8")
        with closing(sqlite3.connect(self.profile / "History")) as db, db:
            db.execute(
                "CREATE TABLE urls (url TEXT, title TEXT, "
                "last_visit_time INTEGER, visit_count INTEGER)"
            )
            db.executemany(
                "INSERT INTO urls VALUES (?, ?, ?, ?)",
                [
                    ("https://gnome.org/", "GNOME", int(self.timestamp), 5),
                    ("chrome://settings", "Settings", int(self.timestamp), 1),
                    ("https://webkitgtk.org/", "WebKitGTK", int(self.timestamp), 2),
                ],
            )

    def test_profiles_use_chrome_display_name(self):
        (self.profile.parent / "Local State").write_text(
            json.dumps(
                {
                    "profile": {"info_cache": {"Default": {"name": "Personal"}}},
                }
            ),
            encoding="utf-8",
        )
        profiles = discover_profiles(self.root / "config")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].label, "Chrome · Personal")

    def test_import_preserves_source_and_is_idempotent(self):
        before = {name: (self.profile / name).read_bytes() for name in ["Bookmarks", "History"]}
        result = import_profile(self.profile, self.library)
        self.assertEqual((result.bookmarks, result.history), (1, 2))
        self.assertEqual(result.warnings, [])
        record = self.library.search("bookmarks")[0]
        self.assertEqual(record["folder"], "Bookmarks bar / Work")
        self.assertEqual(record["created_at"], 1_700_000_000)
        second = import_profile(self.profile, self.library)
        self.assertEqual((second.bookmarks, second.history), (0, 0))
        self.assertEqual(self.library.search("history", "GNOME")[0]["visit_count"], 5)
        for name, data in before.items():
            self.assertEqual((self.profile / name).read_bytes(), data)

    def test_reads_history_with_active_wal(self):
        db = sqlite3.connect(self.profile / "History")
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "INSERT INTO urls VALUES (?, ?, ?, ?)",
            (
                "https://example.org/wal",
                "In WAL",
                int(self.timestamp),
                1,
            ),
        )
        db.commit()
        result = import_profile(self.profile, self.library, bookmarks=False)
        self.assertEqual(result.history, 3)
        self.assertEqual(len(self.library.search("history", "In WAL")), 1)

    def test_import_selection_and_partial_failure(self):
        (self.profile / "History").write_bytes(b"corrupt")
        result = import_profile(self.profile, self.library)
        self.assertEqual(result.bookmarks, 1)
        self.assertEqual(result.history, 0)
        self.assertTrue(result.warnings)
        with self.assertRaises(ValueError):
            import_profile(self.profile, self.library, bookmarks=False)
        with self.assertRaises(ValueError):
            import_profile(self.profile, self.library, bookmarks=False, history=False)

    def test_invalid_bookmarks_and_missing_profile(self):
        (self.profile / "Bookmarks").write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_profile(self.profile, self.library, history=False)
        with self.assertRaises(ValueError):
            import_profile(self.root / "missing", self.library)

    def test_html_export_is_decoded_and_filtered(self):
        html = self.root / "bookmarks.html"
        html.write_text(
            "<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>"
            '<DT><A HREF="https://example.org/?a=1&amp;b=2">A &amp; B</A>'
            '<DT><A HREF="javascript:alert(1)">Bad</A></DL>',
            encoding="utf-8",
        )
        self.assertEqual(import_html(html, self.library).bookmarks, 1)
        self.assertEqual(import_html(html, self.library).bookmarks, 0)
        record = self.library.search("bookmarks")[0]
        self.assertEqual(record["title"], "A & B")
        self.assertEqual(record["url"], "https://example.org/?a=1&b=2")

    def test_unexpected_profile_metadata_does_not_break_discovery(self):
        (self.profile.parent / "Local State").write_text("[]", encoding="utf-8")
        self.assertEqual(len(discover_profiles(self.root / "config")), 1)

    def test_invalid_chrome_times(self):
        for value in [None, "bad", "0", -1]:
            self.assertEqual(chrome_time(value), 0)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Library(Path(self.temp.name) / "private/library.sqlite3")

    def test_history_and_bookmarks_have_different_counts(self):
        self.library.record_visit("https://example.org", "First")
        self.library.record_visit("https://example.org", "Second")
        self.library.add_bookmark("https://example.org", "First")
        self.library.add_bookmark("https://example.org", "Second")
        self.assertEqual(self.library.search("history")[0]["visit_count"], 2)
        self.assertEqual(len(self.library.search("bookmarks")), 1)
        self.assertEqual(self.library.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.library.path.parent.stat().st_mode & 0o777, 0o700)

    def test_reimport_keeps_newer_local_history(self):
        self.library.record_visit("https://example.org", "New")
        self.library.import_records([], [("https://example.org", "Old", 1, 5)])
        record = self.library.search("history")[0]
        self.assertEqual(record["title"], "New")
        self.assertEqual(record["visit_count"], 5)

    def test_search_is_literal(self):
        self.library.add_bookmark("https://example.org/1", "100% useful")
        self.library.add_bookmark("https://example.org/2", "other")
        self.assertEqual(len(self.library.search("bookmarks", "%")), 1)
        self.assertEqual(len(self.library.search("bookmarks", "_")), 0)
        with self.assertRaises(ValueError):
            self.library.search("not_a_table")
