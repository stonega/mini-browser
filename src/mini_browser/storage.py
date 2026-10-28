"""Private, local SQLite library. Connections belong to their calling thread."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .navigation import is_web_url


def home_app_url(value: str) -> str:
    """Validate a launcher URL and normalize its origin without changing the page."""
    if not isinstance(value, str):
        raise ValueError("Home apps require a full http:// or https:// URL.")
    value = value.strip()
    if not is_web_url(value) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Home apps require a full http:// or https:// URL.")
    parts = urlsplit(value)
    if parts.username is not None or parts.password is not None:
        raise ValueError("Home app URLs cannot contain a username or password.")
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    if parts.port and parts.port != (443 if parts.scheme == "https" else 80):
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, parts.fragment))


def bookmark_app_url(value: str) -> str:
    """A bookmarked URL app opens its origin, retaining subdomains and ports."""
    parts = urlsplit(home_app_url(value))
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


class Library:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    folder TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    last_visit REAL NOT NULL,
                    visit_count INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS recent_apps (
                    site TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    last_opened REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS home_apps (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS window_sizes (
                    kind TEXT NOT NULL,
                    app_key TEXT NOT NULL,
                    width INTEGER NOT NULL CHECK (width > 0),
                    height INTEGER NOT NULL CHECK (height > 0),
                    PRIMARY KEY (kind, app_key)
                );
            """)
        path.chmod(0o600)

    def window_size(self, kind: str, app_key: str) -> tuple[int, int] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT width, height FROM window_sizes WHERE kind=? AND app_key=?",
                (kind, app_key),
            ).fetchone()
        if row is None or any(
            type(value) is not int or not 0 < value <= 2**31 - 1 for value in row
        ):
            return None
        return tuple(row)

    def save_window_size(self, kind: str, app_key: str, width: int, height: int):
        if any(type(value) is not int or not 0 < value <= 2**31 - 1 for value in (width, height)):
            raise ValueError("Window dimensions must be positive GTK integers.")
        with self.connect() as db:
            db.execute(
                """INSERT INTO window_sizes VALUES (?, ?, ?, ?)
                   ON CONFLICT(kind, app_key) DO UPDATE SET
                     width=excluded.width, height=excluded.height""",
                (kind, app_key, width, height),
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_records(self, bookmarks, history) -> tuple[int, int]:
        """Atomically merge a snapshot; return newly added record counts."""
        with self.connect() as db:
            before_bookmarks = db.execute("SELECT count(*) FROM bookmarks").fetchone()[0]
            before_history = db.execute("SELECT count(*) FROM history").fetchone()[0]
            db.executemany(
                """INSERT INTO bookmarks VALUES (?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET title=excluded.title, folder=excluded.folder""",
                bookmarks,
            )
            db.executemany(
                """INSERT INTO history VALUES (?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                     title=CASE WHEN excluded.last_visit >= history.last_visit
                           THEN excluded.title ELSE history.title END,
                     last_visit=max(history.last_visit, excluded.last_visit),
                     visit_count=max(history.visit_count, excluded.visit_count)""",
                history,
            )
            return (
                db.execute("SELECT count(*) FROM bookmarks").fetchone()[0] - before_bookmarks,
                db.execute("SELECT count(*) FROM history").fetchone()[0] - before_history,
            )

    def add_bookmark(self, url: str, title: str):
        with self.connect() as db:
            db.execute(
                """INSERT INTO bookmarks VALUES (?, ?, '', ?)
                   ON CONFLICT(url) DO UPDATE SET title=excluded.title""",
                (url, title or url, time.time()),
            )

    def add_bookmark_app(self, url: str):
        url = bookmark_app_url(url)
        self.add_bookmark(url, urlsplit(url).hostname)

    def bookmarked_apps(self) -> list[dict]:
        """Project bookmarks into one launcher per origin, with built-in presets."""
        apps = {}
        for url, title in (
            ("https://www.youtube.com/", "YouTube"),
            ("https://www.bilibili.com/", "Bilibili"),
        ):
            apps[url] = {"url": url, "site": url.rstrip("/"), "title": title}
        for record in self.search("bookmarks", limit=None):
            try:
                url = bookmark_app_url(record["url"])
            except ValueError:
                continue
            apps.setdefault(
                url,
                {"url": url, "site": url.rstrip("/"), "title": urlsplit(url).hostname},
            )
        return list(apps.values())

    def record_visit(self, url: str, title: str):
        visited = time.time()
        with self.connect() as db:
            db.execute(
                """INSERT INTO history VALUES (?, ?, ?, 1)
                   ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                     last_visit=excluded.last_visit, visit_count=history.visit_count+1""",
                (url, title or url, visited),
            )
            if is_web_url(url):
                parts = urlsplit(url)
                host = parts.hostname
                if ":" in host:
                    host = f"[{host}]"
                port = parts.port
                if port and port != (443 if parts.scheme == "https" else 80):
                    host += f":{port}"
                site = f"{parts.scheme}://{host}"
                db.execute(
                    """INSERT INTO recent_apps VALUES (?, ?, ?, ?)
                       ON CONFLICT(site) DO UPDATE SET url=excluded.url,
                         title=excluded.title, last_opened=excluded.last_opened""",
                    (site, url, title or parts.hostname, visited),
                )

    def add_home_app(self, url: str, title: str | None = None) -> dict:
        """Save a durable Home tile without visiting the page or changing history."""
        url = home_app_url(url)
        if title is not None:
            if not isinstance(title, str) or any(ord(char) < 32 for char in title):
                raise ValueError("App title must be plain text between 1 and 200 characters.")
            title = " ".join(title.split())
            if not 1 <= len(title) <= 200:
                raise ValueError("App title must be plain text between 1 and 200 characters.")
        with self.connect() as db:
            db.execute(
                """INSERT INTO home_apps VALUES (?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET title=COALESCE(?, home_apps.title)""",
                (url, title or urlsplit(url).hostname, time.time(), title),
            )
            row = db.execute("SELECT * FROM home_apps WHERE url=?", (url,)).fetchone()
        return self._home_app_record(row)

    @staticmethod
    def _home_app_record(row) -> dict:
        record = dict(row)
        parts = urlsplit(record["url"])
        record["site"] = f"{parts.scheme}://{parts.netloc}"
        return record

    def home_apps(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM home_apps ORDER BY created_at, url").fetchall()
        return [self._home_app_record(row) for row in rows]

    def remove_home_app(self, url: str) -> bool:
        url = home_app_url(url)
        with self.connect() as db:
            return db.execute("DELETE FROM home_apps WHERE url=?", (url,)).rowcount > 0

    def recent_apps(self, limit: int = 6, *, exclude_sites=()) -> list[dict]:
        """Sites actually opened here, grouped by origin, with their latest page."""
        with self.connect() as db:
            excluded = tuple(exclude_sites)
            condition = (
                "WHERE site NOT IN (" + ",".join("?" for _ in excluded) + ")" if excluded else ""
            )
            rows = db.execute(
                f"SELECT * FROM recent_apps {condition} "
                "ORDER BY last_opened DESC, site ASC LIMIT ?",
                (*excluded, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, kind: str, query: str = "", limit: int | None = 200) -> list[dict]:
        if kind not in {"bookmarks", "history"}:
            raise ValueError("Unknown library section")
        order = "created_at" if kind == "bookmarks" else "last_visit"
        # Treat %, _ and backslash as literal search characters.
        query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{query}%"
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT * FROM {kind}
                    WHERE title LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\'
                    ORDER BY {order} DESC, url ASC LIMIT ?""",
                (pattern, pattern, -1 if limit is None else limit),
            ).fetchall()
        return [dict(row) for row in rows]
