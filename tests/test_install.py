import io
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class InstallTests(unittest.TestCase):
    def test_install_is_repeatable_and_preserves_browser_data(self):
        installer = Path(__file__).resolve().parents[1] / "scripts/install"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "home with spaces"
            data = root / ".local/share"
            library = data / "mini-browser/library.sqlite3"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"existing user data")
            with (
                patch.object(Path, "home", return_value=root),
                patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}),
                redirect_stdout(io.StringIO()),
            ):
                runpy.run_path(str(installer), run_name="__main__")
                runpy.run_path(str(installer), run_name="__main__")
            self.assertEqual(library.read_bytes(), b"existing user data")
            executable = root / ".local/bin/mini-browser"
            self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
            desktop = (data / "applications/io.github.stone.MiniBrowser.desktop").read_text()
            self.assertIn(f'Exec="{executable}" %U', desktop)
            self.assertTrue((data / "mini-browser/app/mini_browser/application.py").is_file())
            self.assertTrue(
                (
                    data / "gnome-shell/extensions/mini-browser-pin@io.github.stone/extension.js"
                ).is_file()
            )
