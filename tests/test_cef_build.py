"""Fail closed before using an unverified or wrong-architecture CEF SDK."""

import json
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


class CefBuildTests(unittest.TestCase):
    def test_cached_sdk_archive_is_verified_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "synthetic-sdk.tar.bz2"
            archive.write_bytes(b"tampered synthetic SDK archive")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT / "scripts/build-cef"),
                    "--download-only",
                    "--archive",
                    str(archive),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CEF sha1 mismatch", result.stderr)

    def test_foreign_sdk_cannot_be_used_for_a_native_build(self):
        release = json.loads((PROJECT / "native/cef/release.json").read_text())
        foreign = next(name for name in release["architectures"] if name != platform.machine())
        result = subprocess.run(
            [sys.executable, str(PROJECT / "scripts/build-cef"), "--architecture", foreign],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("native builds are required", result.stderr)

    def test_extracted_sdk_must_match_the_pinned_release(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            (sdk / "include").mkdir()
            (sdk / "include/cef_version.h").write_text('#define CEF_VERSION "outdated"\n')
            result = subprocess.run(
                [sys.executable, str(PROJECT / "scripts/build-cef"), "--sdk", str(sdk)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SDK version differs from the pinned release", result.stderr)
