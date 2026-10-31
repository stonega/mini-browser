"""Saved command-backed web apps and their shared, supervised local servers."""

import http.client
import os
import shlex
import shutil
import socket
import threading
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from .deepseek import DeepSeekHarness, HarnessError
from .storage import home_app_url

_PORT_LOCK = threading.Lock()
_PORTS = set()


def script_port(value):
    if value is None or isinstance(value, str) and not value.strip():
        return 0
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        value = int(value.strip())
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Enter a port from 1 to 65535, or leave it empty for automatic.")
    return value


def script_app_values(title, command, port="", directory=""):
    if not isinstance(title, str) or any(ord(char) < 32 for char in title):
        raise ValueError("Enter an app name between 1 and 200 characters.")
    title = " ".join(title.split())
    if not 1 <= len(title) <= 200:
        raise ValueError("Enter an app name between 1 and 200 characters.")
    if (
        not isinstance(command, str)
        or len(command) > 4096
        or any(ord(char) < 32 for char in command)
    ):
        raise ValueError("Enter a command of up to 4096 characters.")
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        raise ValueError("Check the command's quotes.") from error
    if not arguments or not arguments[0]:
        raise ValueError("Enter the command that starts this app's web server.")
    port = script_port(port)
    url = home_app_url(f"http://127.0.0.1:{port}/") if port else ""
    if not isinstance(directory, str) or any(ord(char) < 32 for char in directory):
        raise ValueError("Choose an existing working folder.")
    folder = Path(directory.strip() or Path.home()).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("Choose an existing working folder using its full path.")
    return title, shlex.join(arguments), url, str(folder)


class ScriptAppStore:
    def __init__(self, library):
        self.library = library
        with library.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS script_apps (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, command TEXT NOT NULL,
                url TEXT NOT NULL, directory TEXT NOT NULL,
                UNIQUE(command, url, directory)
            )""")

    def all(self):
        with self.library.connect() as db:
            return [
                self._record(row) for row in db.execute("SELECT * FROM script_apps ORDER BY rowid")
            ]

    @staticmethod
    def _record(row):
        record = dict(row)
        parts = urlsplit(record["url"])
        # Empty URLs mark automatic allocation. Earlier saved URLs retain their address.
        record["port"] = (
            (parts.port or (443 if parts.scheme == "https" else 80)) if record["url"] else 0
        )
        return record

    def save(self, title, command, port="", directory=""):
        values = script_app_values(title, command, port, directory)
        with self.library.connect() as db:
            db.execute(
                """INSERT INTO script_apps VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(command, url, directory) DO UPDATE SET title=excluded.title""",
                (uuid.uuid4().hex, *values),
            )
            return self._record(
                db.execute(
                    "SELECT * FROM script_apps WHERE command=? AND url=? AND directory=?",
                    values[1:],
                ).fetchone()
            )

    def remove(self, app_id):
        with self.library.connect() as db:
            return db.execute("DELETE FROM script_apps WHERE id=?", (app_id,)).rowcount > 0


class ScriptAppHarness(DeepSeekHarness):
    failed_message = "The app stopped. Check its command and working folder, then try again."
    timeout_message = (
        "The app took too long to start. Check that its command reads PORT and serves that port."
    )

    def __init__(self, record, *, startup_timeout=60):
        super().__init__(startup_timeout=startup_timeout)
        self.record = record
        self.arguments = shlex.split(record["command"])
        self.port = 0
        self.url = None
        self._port_claimed = False

    def _serve(self, cancel):
        try:
            super()._serve(cancel)
        finally:
            with _PORT_LOCK:
                if self._port_claimed:
                    _PORTS.discard(self.port)
                    self._port_claimed = False

    def _reserve_port(self):
        requested = self.record["port"]
        with _PORT_LOCK:
            if requested and requested in _PORTS:
                raise HarnessError("The app port is already in use. Choose another port.")
            for _attempt in range(100):
                try:
                    with socket.socket() as reservation:
                        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        reservation.bind(("127.0.0.1", requested))
                        port = reservation.getsockname()[1]
                except OSError as error:
                    raise HarnessError(
                        "The app port is unavailable or already in use. Choose another port."
                    ) from error
                if port not in _PORTS:
                    _PORTS.add(port)
                    self.port, self._port_claimed = port, True
                    return
            raise HarnessError("No free port is available. Close another local app and try again.")

    def _launch(self):
        # Older saved URLs may use IPv6 or localhost rather than the managed IPv4 address.
        parts = urlsplit(self.record["url"])
        if parts.hostname in {"localhost", "::1"}:
            try:
                connection = socket.create_connection((parts.hostname, self.record["port"]), 0.3)
            except OSError:
                pass
            else:
                connection.close()
                raise HarnessError("The app port is already in use. Choose another port.")
        self._reserve_port()
        self.url = self.record["url"] or f"http://127.0.0.1:{self.port}/"
        executable = Path(self.arguments[0]).expanduser()
        if "/" in self.arguments[0]:
            executable = Path(self.record["directory"]) / executable
            command = shutil.which(str(executable))
        else:
            command = shutil.which(str(executable))
        if not command:
            raise HarnessError(
                "The command was not found. Check its path and that it is executable."
            )
        return (command, *self.arguments[1:]), self.record["directory"]

    def _process_environment(self):
        return {**os.environ, "PORT": str(self.port)}

    def _ready_uri(self, line):
        return None  # Only the managed address can open the app.

    def _poll_ready(self):
        parts = urlsplit(self.url)
        connection_type = (
            http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(parts.hostname, parts.port, timeout=0.3)
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("HEAD", quote(path, safe="/?%:@!$&'()*+,;="))
            response = connection.getresponse()
            if response.status < 500 or response.status == 501:
                return self.url
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        return None
