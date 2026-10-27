"""Run the main Chromium-backed GTK app with disposable browser data."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mini_browser.application import BrowserApplication  # noqa: E402

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mini-browser-example-") as temporary:
        root = Path(temporary)
        app = BrowserApplication(
            data_dir=root / "data", cache_dir=root / "cache", discover_devices=False
        )
        raise SystemExit(app.run([sys.argv[0], *sys.argv[1:]]))
