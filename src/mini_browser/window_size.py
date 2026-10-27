"""Remember normal window dimensions independently for each kind of app."""

import json
import sqlite3
from urllib.parse import urlsplit

from gi.repository import GLib

from .navigation import is_web_url

DEFAULT_SIZE = (420, 780)


def web_app_key(uri):
    """Group paths, schemes and ports by hostname; retain distinct subdomains."""
    if not is_web_url(uri):
        return None
    try:
        host = urlsplit(uri).hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not host:
        return None
    return "web", host


def script_app_key(command):
    """Use the launch command, never a server's temporary URL or access token."""
    return "script", json.dumps(list(command), ensure_ascii=True, separators=(",", ":"))


class WindowSizeMemory:
    def __init__(self, window, library, *, capture=None, resize=None):
        self.window = window
        self.library = library
        self.capture_size = capture or window.get_default_size
        self.resize = resize or window.set_default_size
        self.key = None
        self.size = DEFAULT_SIZE
        self.dirty = False
        self.source = 0
        self.closed = False
        self.restoring = False
        window.connect("notify::default-width", self.capture)
        window.connect("notify::default-height", self.capture)

    def select(self, key):
        if self.closed or key == self.key:
            return
        self.capture()
        self.save()
        self.key = key
        self.size = DEFAULT_SIZE
        self.dirty = False
        if key is not None:
            try:
                self.size = self.library.window_size(*key) or DEFAULT_SIZE
            except (sqlite3.Error, OSError):
                self.window.toast("Could not restore the saved window size.")
        self.restoring = True
        try:
            self.resize(*self.size)
        finally:
            self.restoring = False

    def capture(self, *_):
        if self.closed or self.restoring:
            return
        size = self.capture_size()
        if size is None or min(size) <= 0 or tuple(size) == self.size:
            return
        self.size = tuple(size)
        self.dirty = self.key is not None
        if self.dirty:
            if self.source:
                GLib.source_remove(self.source)
            self.source = GLib.timeout_add(300, self.save)

    def save(self):
        if self.source:
            GLib.source_remove(self.source)
            self.source = 0
        if self.dirty:
            try:
                self.library.save_window_size(*self.key, *self.size)
                self.dirty = False
            except (sqlite3.Error, OSError):
                self.window.toast("Could not save this window size.")
        return GLib.SOURCE_REMOVE

    def close(self):
        if not self.closed:
            self.capture()
            self.save()
            self.closed = True
