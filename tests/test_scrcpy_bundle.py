import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from mini_browser import scrcpy_bundle
from mini_browser.devices import DeviceError, android_tools


class ScrcpyBundleTests(unittest.TestCase):
    def test_release_manifest_and_android_bytecode_are_bundled(self):
        path = scrcpy_bundle.server_path()
        manifest = json.loads((path.parent / "manifest.json").read_text())
        self.assertEqual(manifest["version"], scrcpy_bundle.VERSION)
        self.assertEqual(manifest["sha256"], scrcpy_bundle.SHA256)
        self.assertEqual(path.stat().st_size, manifest["size"])
        with ZipFile(path) as archive:
            self.assertIn("classes.dex", archive.namelist())
        self.assertIn("Apache License", (path.parent / "LICENSE").read_text())

    def test_only_adb_is_required_and_no_desktop_scrcpy_is_looked_up(self):
        with patch("mini_browser.devices.shutil.which", return_value="/test/adb") as which:
            adb, server, version = android_tools()
        which.assert_called_once_with("adb")
        self.assertEqual(adb, "/test/adb")
        self.assertEqual(server, scrcpy_bundle.server_path())
        self.assertEqual(version, scrcpy_bundle.VERSION)

    def test_missing_and_modified_bundles_fail_before_connecting(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(scrcpy_bundle, "BUNDLE", Path(temp)),
                patch("mini_browser.devices.shutil.which", return_value="/test/adb"),
            ):
                with self.assertRaisesRegex(DeviceError, "Reinstall Mini Browser"):
                    android_tools()
                (Path(temp) / "scrcpy-server").write_bytes(b"modified helper")
                with self.assertRaisesRegex(DeviceError, "Reinstall Mini Browser"):
                    android_tools()


if __name__ == "__main__":
    unittest.main()
