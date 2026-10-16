"""Private, local SQLite library. Connections belong to their calling thread."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .navigation import is_web_url


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
            """)
        path.chmod(0o600)

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

    def recent_apps(self, limit: int = 6) -> list[dict]:
        """Sites actually opened here, grouped by origin, with their latest page."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM recent_apps ORDER BY last_opened DESC, site ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, kind: str, query: str = "", limit: int = 200) -> list[dict]:
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
                    ORDER BY {order} DESC LIMIT ?""",
                (pattern, pattern, limit),
            ).fetchall()
        return [dict(row) for row in rows]
