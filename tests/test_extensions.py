"""Unpacked extension metadata, repeat registration and persisted startup choices."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_browser.extensions import (
    MAX_MANIFEST_BYTES,
    ExtensionStore,
    extension_ui,
    extension_values,
)
from mini_browser.storage import Library


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "data/library.sqlite3")
        self.store = ExtensionStore(self.library)
        self.folder = self.root / "Synthetic extension"
        self.folder.mkdir()
        self.manifest = {"manifest_version": 3, "name": "Synthetic extension", "version": "1.0"}
        self.write_manifest(self.manifest)

    def write_manifest(self, manifest):
        (self.folder / "manifest.json").write_text(json.dumps(manifest))

    def test_registration_deduplicates_canonical_path_and_survives_reopen(self):
        source = (self.folder / "manifest.json").read_bytes()
        path = self.store.add(self.folder)
        alias = self.root / "alias"
        alias.symlink_to(self.folder, target_is_directory=True)
        self.store.add(alias)
        reopened = ExtensionStore(Library(self.library.path))
        self.assertEqual(len(reopened.all()), 1)
        self.assertEqual(reopened.startup(), ((path,), {}))
        self.assertEqual((self.folder / "manifest.json").read_bytes(), source)

    def test_enable_disable_remove_preserve_source_and_browser_data(self):
        self.library.add_home_app("https://example.org/", "Synthetic website")
        path = self.store.add(self.folder)
        self.store.set_enabled(path, False)
        self.assertEqual(ExtensionStore(self.library).startup(), ((), {}))
        self.store.set_enabled(path, True)
        self.assertEqual(self.store.startup(), ((path,), {}))
        self.store.remove(path)
        self.assertEqual(self.store.all(), [])
        self.assertTrue((self.folder / "manifest.json").is_file())
        self.assertEqual(len(self.library.home_apps()), 1)

    def test_readding_refreshes_metadata_and_enables(self):
        path = self.store.add(self.folder)
        self.store.set_enabled(path, False)
        self.write_manifest({**self.manifest, "name": "Updated fixture", "version": "2.0"})
        self.store.add(self.folder)
        self.assertEqual(self.store.all()[0]["name"], "Updated fixture")
        self.assertEqual(self.store.all()[0]["version"], "2.0")
        self.assertEqual(self.store.all()[0]["enabled"], 1)

    def test_invalid_manifests_are_rejected_without_registering(self):
        for manifest in (
            [],
            None,
            {},
            {**self.manifest, "manifest_version": 2},
            {**self.manifest, "manifest_version": True},
            {**self.manifest, "manifest_version": 3.0},
            {**self.manifest, "name": ""},
            {**self.manifest, "name": 123},
            {**self.manifest, "name": "Invalid\x00name"},
            {**self.manifest, "name": "Invalid\ud800name"},
            {**self.manifest, "version": "Invalid\x00version"},
            {**self.manifest, "version": []},
        ):
            with self.subTest(manifest=manifest):
                self.write_manifest(manifest)
                with self.assertRaises(ValueError):
                    self.store.add(self.folder)
                self.assertEqual(self.store.all(), [])
        for raw in (
            b"{broken",
            b"\xff",
            b" " * (MAX_MANIFEST_BYTES + 1),
            b"[" * 10000 + b"]" * 10000,
        ):
            (self.folder / "manifest.json").write_bytes(raw)
            with self.assertRaises(ValueError):
                self.store.add(self.folder)

    def test_missing_folder_and_comma_paths_are_actionable(self):
        with self.assertRaisesRegex(ValueError, "readable unpacked"):
            self.store.add(self.root / "missing")
        comma = self.root / "comma,folder"
        self.folder.rename(comma)
        with self.assertRaisesRegex(ValueError, "without commas"):
            self.store.add(comma)

    def test_missing_or_changed_sources_do_not_break_browser_startup(self):
        path = self.store.add(self.folder)
        (self.folder / "manifest.json").unlink()
        paths, errors = self.store.startup()
        self.assertEqual(paths, ())
        self.assertIn(path, errors)
        self.store.set_enabled(path, False)
        with self.assertRaises(ValueError):
            self.store.set_enabled(path, True)
        self.assertEqual(self.store.all()[0]["enabled"], 0)
        self.store.remove(path)
        self.assertEqual(self.store.all(), [])

    def test_failed_metadata_read_does_not_register(self):
        with patch("pathlib.Path.open", side_effect=PermissionError):
            with self.assertRaises(ValueError):
                self.store.add(self.folder)
        self.assertEqual(self.store.all(), [])

    def test_localized_names_have_readable_fallback(self):
        self.write_manifest({**self.manifest, "name": "__MSG_appName__"})
        self.assertEqual(extension_values(self.folder)[1], self.folder.name)

    def test_popup_identity_uses_canonical_linux_path_or_public_key(self):
        (self.folder / "popup.html").write_text("<!doctype html><title>Synthetic popup</title>")
        self.write_manifest({**self.manifest, "action": {"default_popup": "popup.html"}})
        ui = extension_ui(self.folder)
        expected = (
            hashlib.sha256(bytes(self.folder))
            .hexdigest()[:32]
            .translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
        )
        self.assertEqual(ui["id"], expected)
        self.assertEqual(ui["popup"], f"chrome-extension://{expected}/popup.html")
        key = b"synthetic-public-key"
        self.write_manifest({**self.manifest, "key": base64.b64encode(key).decode()})
        first = extension_ui(self.folder)
        other = self.root / "Other folder"
        other.mkdir()
        (other / "manifest.json").write_bytes((self.folder / "manifest.json").read_bytes())
        self.assertEqual(first["id"], extension_ui(other)["id"])
        self.assertNotEqual(first["id"], expected)
        self.assertIsNone(first["popup"])
        self.write_manifest(
            {
                **self.manifest,
                "key": "-----BEGIN PUBLIC KEY-----\n"
                + base64.b64encode(key).decode()
                + "\n-----END PUBLIC KEY-----",
            }
        )
        self.assertEqual(first["id"], extension_ui(self.folder)["id"])

    def test_popup_paths_cannot_escape_or_load_remote_content(self):
        (self.folder / "popup.html").write_text("<!doctype html>")
        outside = self.root / "outside.html"
        outside.write_text("<!doctype html>")
        (self.folder / "link.html").symlink_to(outside)
        for popup in (
            "https://example.org/popup",
            "//example.org/popup",
            "../outside.html",
            "%2e%2e/outside.html",
            "folder/../../outside.html",
            "link.html",
            "missing.html",
            "file:///tmp/outside.html",
            "folder\\outside.html",
            123,
            "bad\x00.html",
        ):
            with self.subTest(popup=popup):
                self.write_manifest({**self.manifest, "action": {"default_popup": popup}})
                with self.assertRaises(ValueError):
                    extension_ui(self.folder)
        self.write_manifest({**self.manifest, "action": {"default_popup": "/popup.html?a=1#tab"}})
        self.assertTrue(extension_ui(self.folder)["popup"].endswith("/popup.html?a=1#tab"))

    def test_invalid_keys_and_action_metadata_are_actionable(self):
        for value in ("!bad", "", [], 123):
            self.write_manifest({**self.manifest, "key": value})
            with self.assertRaisesRegex(ValueError, "public key"):
                extension_ui(self.folder)
        self.write_manifest({**self.manifest, "action": []})
        with self.assertRaisesRegex(ValueError, "action declaration"):
            extension_ui(self.folder)
