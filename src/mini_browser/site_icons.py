"""Small, private PNG stores; shared with the headless launcher, without GTK."""

import hashlib
import json
import os
import re
import struct
import tempfile
from pathlib import Path

from .storage import bookmark_app_url

MAX_ICON_BYTES = 1024 * 1024


def valid_png(data):
    if not 24 <= len(data) <= MAX_ICON_BYTES or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        return False
    width, height = struct.unpack(">II", data[16:24])
    return 0 < width <= 256 and 0 < height <= 256


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def save_custom_icon(library, data):
    if not valid_png(data):
        raise ValueError("Choose an image that can be converted to a small app icon.")
    name = hashlib.sha256(data).hexdigest() + ".png"
    write_private(library.path.parent / "app-icons" / name, data)
    return name


class SiteIconCache:
    def __init__(self, directory=None):
        self.directory = (
            Path(directory)
            if directory
            else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            / "mini-browser/site-icons"
        )

    @staticmethod
    def _key(url):
        return hashlib.sha256(bookmark_app_url(url).encode()).hexdigest()

    def path_for(self, url):
        try:
            key = self._key(url)
            index = self.directory / (key + ".json")
            if index.stat().st_size > 256:
                return None
            name = json.loads(index.read_text())
            if not isinstance(name, str) or not re.fullmatch(key + r"-[a-f0-9]{64}\.png", name):
                return None
            path = self.directory / name
            if path.stat().st_size > MAX_ICON_BYTES:
                return None
            with path.open("rb") as source:
                return path if valid_png(source.read(24)) else None
        except (OSError, ValueError, TypeError):
            return None

    def save(self, url, data):
        if not valid_png(data):
            raise ValueError("Invalid website icon.")
        key = self._key(url)
        name = key + "-" + hashlib.sha256(data).hexdigest() + ".png"
        previous = self.path_for(url)
        if previous and previous.name == name:
            return False
        write_private(self.directory / name, data)
        write_private(self.directory / (key + ".json"), json.dumps(name).encode())
        if previous:
            previous.unlink(missing_ok=True)
        return True
