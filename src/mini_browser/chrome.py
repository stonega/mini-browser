"""Read Chrome bookmarks and history without changing the source profile."""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .navigation import is_web_url
from .storage import Library

CHROME_EPOCH_OFFSET = 11_644_473_600
HISTORY_LIMIT = 50_000


@dataclass(frozen=True)
class ChromeProfile:
    path: Path
    name: str
    browser: str = "Chrome"

    @property
    def label(self):
        return f"{self.browser} · {self.name}"


@dataclass
class ImportResult:
    bookmarks: int = 0
    history: int = 0
    warnings: list[str] = field(default_factory=list)


def discover_profiles(config_dir: Path | None = None) -> list[ChromeProfile]:
    config_dir = config_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    roots = [
        (config_dir / "google-chrome", "Chrome"),
        (config_dir / "google-chrome-beta", "Chrome Beta"),
        (config_dir / "google-chrome-unstable", "Chrome Dev"),
        (config_dir / "chromium", "Chromium"),
    ]
    if config_dir == Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")):
        roots.extend(
            [
                (
                    Path.home() / ".var/app/com.google.Chrome/config/google-chrome",
                    "Chrome (Flatpak)",
                ),
                (
                    Path.home() / ".var/app/org.chromium.Chromium/config/chromium",
                    "Chromium (Flatpak)",
                ),
            ]
        )
    profiles = []
    for root, browser in roots:
        if not root.is_dir():
            continue
        try:
            state = json.loads((root / "Local State").read_text(encoding="utf-8"))
            names = state.get("profile", {}).get("info_cache", {})
        except (OSError, ValueError, AttributeError):
            names = {}
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for path in children:
            if path.is_dir() and ((path / "Bookmarks").is_file() or (path / "History").is_file()):
                entry = names.get(path.name, {}) if isinstance(names, dict) else {}
                name = entry.get("name", path.name) if isinstance(entry, dict) else path.name
                profiles.append(ChromeProfile(path, str(name), browser))
    return profiles


def chrome_time(value) -> float:
    try:
        timestamp = int(value) / 1_000_000 - CHROME_EPOCH_OFFSET
        return max(0, min(timestamp, time.time()))
    except (ValueError, TypeError, OverflowError):
        return 0


def read_bookmarks(path: Path) -> list[tuple]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("roots"), dict):
        raise ValueError("Invalid Chrome bookmarks file")
    records = []

    def walk(node, folders=()):
        if not isinstance(node, dict):
            return
        if node.get("type") == "url":
            url = node.get("url", "")
            if isinstance(url, str) and is_web_url(url):
                records.append(
                    (
                        url,
                        str(node.get("name") or url),
                        " / ".join(folders),
                        chrome_time(node.get("date_added")),
                    )
                )
        else:
            name = node.get("name")
            child_folders = (*folders, str(name)) if name else folders
            children = node.get("children", [])
            if isinstance(children, list):
                for child in children:
                    walk(child, child_folders)

    for root in data["roots"].values():
        walk(root)
    return records


def read_history(path: Path) -> list[tuple]:
    # mode=ro reads Chrome's WAL consistently and cannot rewrite the database.
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        rows = db.execute(
            """SELECT url, title, last_visit_time, visit_count FROM urls
               ORDER BY last_visit_time DESC LIMIT ?""",
            (HISTORY_LIMIT,),
        )
        return [
            (url, title or url, chrome_time(visited), max(1, int(count or 1)))
            for url, title, visited, count in rows
            if isinstance(url, str) and is_web_url(url)
        ]
    finally:
        db.close()


def import_profile(
    profile: Path,
    library: Library,
    *,
    bookmarks: bool = True,
    history: bool = True,
) -> ImportResult:
    if not bookmarks and not history:
        raise ValueError("Select bookmarks or history to import.")
    result = ImportResult()
    bookmark_records, history_records = [], []
    succeeded = False
    for enabled, filename, reader in [
        (bookmarks, "Bookmarks", read_bookmarks),
        (history, "History", read_history),
    ]:
        if not enabled:
            continue
        try:
            records = reader(profile / filename)
            if filename == "Bookmarks":
                bookmark_records = records
            else:
                history_records = records
                if len(records) == HISTORY_LIMIT:
                    result.warnings.append(
                        "History is limited to the 50,000 most recent addresses."
                    )
            succeeded = True
        except (OSError, ValueError, sqlite3.Error, RecursionError):
            result.warnings.append(f"Could not read {filename}. Check the profile or close Chrome.")
    if not succeeded:
        raise ValueError(" ".join(result.warnings))
    result.bookmarks, result.history = library.import_records(bookmark_records, history_records)
    return result


class BookmarkHTMLParser(HTMLParser):
    """Accept Netscape-format bookmark exports, ignoring scripts and unsafe URLs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records = []
        self.url = None
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs = dict(attrs)
            url = attrs.get("href", "")
            self.url = url if is_web_url(url) else None
            self.title = []

    def handle_data(self, data):
        if self.url:
            self.title.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.url:
            title = " ".join("".join(self.title).split()) or self.url
            self.records.append((self.url, title, "Imported HTML", time.time()))
            self.url = None


def import_html(path: Path, library: Library) -> ImportResult:
    parser = BookmarkHTMLParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    if not parser.records:
        raise ValueError("This file contains no http:// or https:// bookmarks.")
    added, _ = library.import_records(parser.records, [])
    return ImportResult(bookmarks=added)
