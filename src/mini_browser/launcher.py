"""Read the panel launcher's app names without starting GTK or Chromium."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from .storage import BOOKMARK_PRESETS, Library


def app_catalog(library=None):
    """Expose launch IDs and labels only, never script commands or browsing history."""
    websites = (
        library.web_apps()
        if library is not None
        else [{"url": url, "title": title} for url, title in BOOKMARK_PRESETS]
    )
    records = [
        {"id": "web:" + app["url"], "title": app["title"], "category": "websites"}
        for app in websites
    ]
    records.append(
        {
            "id": "builtin:deepseek",
            "title": "DeepSeek Harness",
            "category": "scripts",
            "icon": str(Path(__file__).with_name("icons") / "deepseek.svg"),
        }
    )
    if library is not None:
        with library.connect() as db:
            # Profiles created before script apps may not have this optional table yet.
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='script_apps'").fetchone():
                records.extend(
                    {"id": "script:" + row["id"], "title": row["title"], "category": "scripts"}
                    for row in db.execute("SELECT id, title FROM script_apps ORDER BY rowid")
                )
    return sorted(records, key=lambda app: (app["title"].casefold(), app["id"]))


def main(arguments=None):
    if arguments:
        print("Usage: mini-browser list-apps", file=sys.stderr)
        return 2
    directory = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mini-browser"
    path = directory / "library.sqlite3"
    try:
        catalog = app_catalog(Library(path, readonly=True) if path.exists() else None)
    except (OSError, sqlite3.Error, ValueError):
        print("Could not read Mini Browser apps. Open Mini Browser and try again.", file=sys.stderr)
        return 1
    print(json.dumps(catalog, ensure_ascii=False))
    return 0
