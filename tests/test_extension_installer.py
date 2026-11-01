"""Authenticate downloaded code and install it atomically using synthetic publishers."""

import http.client
import io
import json
import shutil
import sqlite3
import stat
import struct
import tempfile
import unittest
import urllib.error
import warnings
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from extension_fixture import signed_package

from mini_browser import extension_installer as installer
from mini_browser.extensions import ExtensionStore, extension_ui
from mini_browser.storage import Library


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ExtensionStore(Library(self.root / "data/library.sqlite3"))
        self.installer = installer.ExtensionInstaller(
            self.store, self.root / "extensions", "156.0.1.2"
        )
        self.identifier, self.data = signed_package(self.key)
        self.entry = {"id": self.identifier, "name": "Catalog fixture"}
        self.package = self.root / "fixture.crx"
        self.package.write_bytes(self.data)

    def download(self, _url, destination, _progress, _cancelled):
        shutil.copyfile(self.package, destination)

    def install(self, **kwargs):
        with patch.object(installer, "download_package", side_effect=self.download):
            return Path(self.installer.install(self.entry, **kwargs))

    def assert_empty(self):
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.store.market_installs(), {})
        self.assertFalse(list(self.installer.directory.glob("**/manifest.json")))
        self.assertFalse(list(self.installer.directory.glob(".install-*")))

    def test_signed_install_preserves_identity_and_original_package_across_reopen(self):
        progress = []
        path = self.install(progress=progress.append)
        self.assertEqual(extension_ui(path)["id"], self.identifier)
        self.assertEqual(path.with_suffix(".crx").read_bytes(), self.data)
        reopened = ExtensionStore(Library(self.store.library.path))
        self.assertEqual(reopened.market_installs(), {self.identifier: str(path)})
        self.assertEqual(reopened.startup(), ((str(path),), {}))
        self.assertIn("Verifying signature…", progress)
        with patch.object(installer, "download_package") as download:
            self.assertEqual(self.installer.install(self.entry), str(path))
            download.assert_not_called()
        self.assertEqual(len(self.store.all()), 1)
        self.store.remove(str(path))
        self.assertEqual(self.store.market_installs(), {})
        self.assertTrue(path.is_dir())
        self.assertTrue(path.with_suffix(".crx").is_file())

    def test_localized_name_uses_catalog_name_in_managed_copy(self):
        _, data = signed_package(
            self.key, {"manifest_version": 3, "name": "__MSG_name__", "version": "1.0"}
        )
        self.package.write_bytes(data)
        path = self.install()
        self.assertEqual(
            json.loads((path / "manifest.json").read_text())["name"], "Catalog fixture"
        )
        self.assertEqual(path.with_suffix(".crx").read_bytes(), data)

    def test_ecdsa_publisher_proof_is_verified(self):
        identifier, data = signed_package(ec.generate_private_key(ec.SECP256R1()))
        self.package.write_bytes(data)
        self.assertTrue(installer.verify_package(self.package, identifier))

    def test_wrong_identity_tampered_payload_and_signature_are_rejected(self):
        cases = [("a" * 32, self.data)]
        tampered = bytearray(self.data)
        tampered[-1] ^= 1
        cases.append((self.identifier, bytes(tampered)))
        tampered = bytearray(self.data)
        tampered[350] ^= 1  # Within the RSA signature, outside the public key.
        cases.append((self.identifier, bytes(tampered)))
        for identifier, data in cases:
            with self.subTest(identifier=identifier, size=len(data)):
                self.package.write_bytes(data)
                self.entry["id"] = identifier
                with self.assertRaises(installer.InstallError):
                    self.install()
                self.assert_empty()

    def test_truncated_or_unsupported_header_is_rejected(self):
        for data in (
            b"",
            b"Cr24" + struct.pack("<II", 2, 1) + b"x",
            b"Cr24" + struct.pack("<II", 3, installer.MAX_HEADER + 1),
            self.data[:20],
            b"Cr24" + struct.pack("<II", 3, 1) + b"\x00",
        ):
            with self.subTest(data=data):
                self.package.write_bytes(data)
                with self.assertRaises(installer.InstallError):
                    self.install()
                self.assert_empty()

    def test_invalid_or_v2_manifest_does_not_register(self):
        for manifest in (
            {"manifest_version": 2, "name": "Fixture", "version": "1"},
            {"manifest_version": 3, "name": 123, "version": "1"},
            {
                "manifest_version": 3,
                "name": "Fixture",
                "version": "1",
                "action": {"default_popup": "missing"},
            },
        ):
            self.package.write_bytes(signed_package(self.key, manifest)[1])
            with self.assertRaises(ValueError):
                self.install()
            self.assert_empty()

    def test_failed_registration_rolls_back_promoted_files(self):
        with patch.object(self.store, "register_market", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                self.install()
        self.assert_empty()
        self.assertFalse(list(self.installer.directory.glob("**/*.crx")))

    def test_failed_replacement_keeps_existing_registry_and_original_files(self):
        previous = self.install()
        (previous / "manifest.json").unlink()
        with patch.object(self.store, "register_market", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                self.install()
        self.assertEqual(self.store.market_installs(), {self.identifier: str(previous)})
        self.assertEqual(len(self.store.all()), 1)
        replacement = self.install()
        self.assertNotEqual(replacement, previous)
        self.assertEqual(len(self.store.all()), 1)
        self.assertTrue(previous.with_suffix(".crx").is_file())

    def test_cancellation_and_failed_download_leave_no_installed_files(self):
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.install(cancelled=lambda: True)
        self.assert_empty()
        with patch.object(installer, "download_package", side_effect=OSError):
            with self.assertRaises(OSError):
                self.installer.install(self.entry)
        self.assert_empty()

    def test_unsafe_archives_are_rejected_without_writing_outside_staging(self):
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ("../outside", "/outside", "folder/../../outside", "a\\outside", "a:b", link):
            with self.subTest(name=name):
                self.package.write_bytes(signed_package(self.key, files=[(name, "target")])[1])
                with self.assertRaises(installer.InstallError):
                    self.install()
                self.assert_empty()
                self.assertFalse((self.root / "outside").exists())

    def test_duplicate_files_and_archive_limits_are_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.package.write_bytes(signed_package(self.key, files=[("manifest.json", "bad")])[1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.install()
        self.assert_empty()
        self.package.write_bytes(self.data)
        for limit in ("MAX_FILES", "MAX_UNPACKED"):
            with patch.object(installer, limit, 0):
                with self.assertRaisesRegex(ValueError, "limits"):
                    self.install()
            self.assert_empty()


class DownloadTests(unittest.TestCase):
    def test_only_official_https_download_sources_are_allowed(self):
        url = installer.package_url("a" * 32, "156.0.1.2")
        self.assertIn("id%3D" + "a" * 32 + "%26uc", url)
        for url in (
            "http://clients2.google.com/file",
            "https://clients2.google.com.evil.example/file",
            "https://example.org/file",
            "https://user@clients2.google.com/file",
            "https://clients2.google.com:444/file",
        ):
            with self.subTest(url=url), self.assertRaises(installer.InstallError):
                installer._StoreRedirect().redirect_request(None, None, 302, "", {}, url)

    def test_download_limits_incomplete_response_and_connection_errors(self):
        url = installer.package_url("a" * 32, "156.0.1.2")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "download.crx"
            for headers, payload, limit in (
                ({"Content-Length": "10"}, b"short", 100),
                ({}, b"large", 4),
                ({"Content-Length": "10"}, b"", 4),
            ):
                response = io.BytesIO(payload)
                response.headers = headers
                response.geturl = lambda: url
                opener = Mock()
                opener.open.return_value = response
                with patch.object(installer.urllib.request, "build_opener", return_value=opener):
                    with patch.object(installer, "MAX_DOWNLOAD", limit):
                        with self.assertRaises(installer.InstallError):
                            installer.download_package(url, target, lambda _: None, lambda: False)
                target.unlink(missing_ok=True)
            for error in (
                urllib.error.URLError("offline"),
                TimeoutError(),
                http.client.IncompleteRead(b"short"),
            ):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(installer.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(ValueError, "connection"):
                        installer.download_package(url, target, lambda _: None, lambda: False)

    def test_complete_download_reports_progress_and_keeps_package(self):
        url = installer.package_url("a" * 32, "156.0.1.2")
        response = io.BytesIO(b"synthetic package")
        response.headers = {"Content-Length": "17"}
        response.geturl = lambda: "https://clients2.googleusercontent.com/package.crx"
        opener = Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "package.crx"
            progress = []
            with patch.object(installer.urllib.request, "build_opener", return_value=opener):
                installer.download_package(url, target, progress.append, lambda: False)
            self.assertEqual(target.read_bytes(), b"synthetic package")
            self.assertEqual(progress, ["Downloading 100%"])
