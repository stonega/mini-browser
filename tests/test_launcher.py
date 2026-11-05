"""Panel catalogs share Home's app rules and never need a GUI or writable profile."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_browser.launcher import app_catalog
from mini_browser.script_apps import ScriptAppStore
from mini_browser.storage import Library


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "cache")})
        environment.start()
        self.addCleanup(environment.stop)
        self.path = self.root / "mini-browser/library.sqlite3"
        self.library = Library(self.path)

    def test_catalog_matches_home_deduplication_and_removal(self):
        self.library.add_bookmark_app("https://example.org/bookmarked-page")
        saved = self.library.add_home_app("https://example.org/docs?x=1", "Docs 中文 <app>")
        self.library.add_bookmark_app("https://other.example/docs")
        self.library.uninstall_web_app("https://www.youtube.com/")
        records = app_catalog(self.library)
        websites = [record for record in records if record["category"] == "websites"]
        self.assertEqual(
            {record["id"] for record in websites},
            {"web:" + app["url"] for app in self.library.web_apps()},
        )
        self.assertIn(
            {
                "id": "web:" + saved["url"],
                "title": saved["title"],
                "category": "websites",
                "avatar": {"text": "example.org", "initials": "E", "color": 8},
            },
            records,
        )
        self.assertNotIn("web:https://example.org/", {record["id"] for record in websites})
        self.library.uninstall_web_app(saved["url"])
        self.assertNotIn(
            "web:" + saved["url"], {record["id"] for record in app_catalog(self.library)}
        )

    def test_only_app_metadata_is_exposed_and_script_removal_is_current(self):
        script = ScriptAppStore(self.library).save(
            "Local demo", "python3 -m http.server", "", str(self.root)
        )
        self.library.record_visit("https://history-only.example/private", "Private visit")
        records = app_catalog(self.library)
        self.assertIn(
            {"id": "script:" + script["id"], "title": "Local demo", "category": "scripts"}, records
        )
        encoded = json.dumps(records)
        for private in ("history-only", "Private visit", "http.server", str(self.root)):
            self.assertNotIn(private, encoded)
        ScriptAppStore(self.library).remove(script["id"])
        self.assertNotIn(
            "script:" + script["id"], {record["id"] for record in app_catalog(self.library)}
        )

    def test_readonly_library_never_initializes_or_changes_data(self):
        self.library.add_home_app("https://example.org/", "Example")
        before = self.path.read_bytes()
        parent_mode, file_mode = self.path.parent.stat().st_mode, self.path.stat().st_mode
        readonly = Library(self.path, readonly=True)
        self.assertEqual(app_catalog(readonly), app_catalog(self.library))
        with self.assertRaises(sqlite3.OperationalError):
            readonly.add_home_app("https://write.example/", "Not allowed")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(
            (self.path.parent.stat().st_mode, self.path.stat().st_mode), (parent_mode, file_mode)
        )
        missing = self.root / "missing/library.sqlite3"
        with self.assertRaises(sqlite3.OperationalError), Library(missing, readonly=True).connect():
            pass
        self.assertFalse(missing.parent.exists())

    def command(self, *arguments, data=None):
        return subprocess.run(
            [sys.executable, "-m", "mini_browser", "list-apps", *arguments],
            env={
                **os.environ,
                "XDG_DATA_HOME": str(data or self.root),
                "DISPLAY": "",
                "WAYLAND_DISPLAY": "",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_cli_reads_catalog_headlessly_without_chromium_or_profile_creation(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), app_catalog(self.library))
        missing = self.root / "fresh"
        result = self.command(data=missing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), app_catalog())
        self.assertFalse(missing.exists())
        self.assertEqual(self.command("unexpected").returncode, 2)

    def test_cli_reports_corrupt_catalog_without_private_error_details(self):
        self.path.write_bytes(b"synthetic invalid SQLite file")
        result = self.command()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Open Mini Browser", result.stderr)
        self.assertNotIn(str(self.root), result.stderr)
