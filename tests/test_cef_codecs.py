"""Codec packages must be verified before native code or resources are used."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cef_codecs", PROJECT / "scripts/cef_codecs.py")
codecs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codecs)


class CefCodecTests(unittest.TestCase):
    def test_cached_packages_are_verified_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cef-codec-packages"
            cache.mkdir()
            archive = cache / "fixture.pkg.tar.zst"
            archive.write_bytes(b"tampered package")
            package = {
                "filename": archive.name,
                "sha256": hashlib.sha256(b"expected package").hexdigest(),
                "mirrors": [],
            }
            with patch.object(codecs, "extract_package") as extract:
                with self.assertRaisesRegex(RuntimeError, "sha256 mismatch"):
                    codecs.prepare_codec_sdk(root / "sdk", root, {"runtime_packages": [package]})
                extract.assert_not_called()

    def test_codec_headers_must_match_the_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "package"
            archive.write_bytes(b"synthetic verified package")

            def extract(_archive, target):
                header = target / "usr/include/cef/include/cef_version.h"
                header.parent.mkdir(parents=True)
                header.write_text('#define CEF_VERSION "different version"\n')

            release = {"version": "pinned version", "runtime_packages": [{"name": "cef"}]}
            with (
                patch.object(codecs, "verified_package", return_value=archive),
                patch.object(codecs, "extract_package", side_effect=extract),
            ):
                with self.assertRaisesRegex(RuntimeError, "does not match the SDK"):
                    codecs.prepare_codec_sdk(root / "sdk", root, release)
