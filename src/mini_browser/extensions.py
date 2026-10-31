"""Unpacked Chrome extension registration, independent of GTK and source files."""

import base64
import binascii
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

MAX_MANIFEST_BYTES = 1024 * 1024


def _extension_metadata(directory):
    """Read basic MV3 metadata; Chromium validates the remaining manifest at startup."""
    try:
        folder = Path(directory).expanduser().resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("Choose the unpacked folder containing manifest.json.")
        # Chromium's load-extension switch uses commas as path separators.
        if "," in str(folder) or any(ord(char) < 32 for char in str(folder)):
            raise ValueError("Move the extension to a folder without commas or control characters.")
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Choose a readable unpacked extension with a manifest.json file.")
        with manifest_path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("The extension manifest is too large (maximum 1 MiB).")
        manifest = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
        raise ValueError(
            "Choose a readable unpacked extension with a valid manifest.json."
        ) from error
    if not isinstance(manifest, dict) or type(manifest.get("manifest_version")) is not int:
        raise ValueError("The extension needs a Manifest V3 manifest.json.")
    if manifest["manifest_version"] != 3:
        raise ValueError("Only Manifest V3 Chrome extensions are supported.")
    name, version = manifest.get("name"), manifest.get("version")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 200
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in name)
    ):
        raise ValueError("The extension manifest needs a name of up to 200 characters.")
    if (
        not isinstance(version, str)
        or not version.strip()
        or len(version) > 100
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in version)
    ):
        raise ValueError("The extension manifest needs a version of up to 100 characters.")
    # Chromium resolves translated names itself. Use the folder name for these
    # manifests rather than displaying their internal __MSG_...__ placeholder.
    if name.startswith("__MSG_") and name.endswith("__"):
        name = folder.name
    return folder, manifest, " ".join(name.split()), version.strip()


def extension_values(directory):
    folder, _manifest, name, version = _extension_metadata(directory)
    return str(folder), name, version


def extension_ui(directory):
    """Resolve Chromium's Linux extension ID and a local, declared action popup."""
    folder, manifest, name, _version = _extension_metadata(directory)
    identifier = _extension_id(folder, manifest)
    origin = f"chrome-extension://{identifier}"
    return {
        "path": str(folder),
        "id": identifier,
        "name": name,
        "origin": origin,
        "popup": _popup_url(folder, manifest, origin),
    }


def _extension_id(folder, manifest):
    identity = os.fsencode(folder)
    if "key" in manifest:
        key = manifest["key"]
        if not isinstance(key, str):
            raise ValueError("The extension's public key is invalid.")
        if key.startswith("-----BEGIN"):
            key = re.sub(r"-----BEGIN [^-]+-----|-----END [^-]+-----", "", key)
        try:
            identity = base64.b64decode("".join(key.split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("The extension's public key is invalid.") from error
        if not identity:
            raise ValueError("The extension's public key is empty.")
    # Chromium components/crx_file/id_util.cc: first 128 SHA-256 bits, a–p alphabet.
    digest = hashlib.sha256(identity).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def _popup_url(folder, manifest, origin):
    action = manifest.get("action", {})
    if not isinstance(action, dict):
        raise ValueError("The extension's action declaration is invalid.")
    popup = action.get("default_popup", "")
    if not isinstance(popup, str) or any(
        ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in popup
    ):
        raise ValueError("The extension's popup must name a local HTML file.")
    url = None
    if popup:
        parts = urlsplit(popup)
        path = unquote(parts.path)
        if (
            parts.scheme
            or parts.netloc
            or "\\" in path
            or ".." in path.split("/")
            or not path.lstrip("/")
        ):
            raise ValueError("The extension's popup must stay inside its folder.")
        try:
            target = (folder / path.lstrip("/")).resolve(strict=True)
            if not target.is_relative_to(folder) or not target.is_file():
                raise ValueError("The extension's popup must be a file inside its folder.")
        except (OSError, RuntimeError) as error:
            raise ValueError("The extension's popup file is missing or unreadable.") from error
        url = origin + "/" + quote(path.lstrip("/"), safe="/-._~")
        if parts.query:
            url += "?" + quote(parts.query, safe="!$&'()*+,-./:;=?@_%~")
        if parts.fragment:
            url += "#" + quote(parts.fragment, safe="!$&'()*+,-./:;=?@_%~")
    return url


class ExtensionStore:
    def __init__(self, library):
        self.library = library
        with library.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chrome_extensions (
                path TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
            )""")

    def all(self):
        with self.library.connect() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM chrome_extensions ORDER BY name, path")
            ]

    def add(self, directory):
        values = extension_values(directory)
        with self.library.connect() as db:
            db.execute(
                """INSERT INTO chrome_extensions (path, name, version) VALUES (?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       name=excluded.name, version=excluded.version, enabled=1""",
                values,
            )
        return values[0]

    def set_enabled(self, path, enabled):
        # Validate again on enable in case the source folder was moved or edited.
        if enabled:
            extension_values(path)
        with self.library.connect() as db:
            db.execute("UPDATE chrome_extensions SET enabled=? WHERE path=?", (bool(enabled), path))

    def remove(self, path):
        with self.library.connect() as db:
            db.execute("DELETE FROM chrome_extensions WHERE path=?", (path,))

    def startup(self):
        """Skip unavailable/invalid extensions without preventing browser startup."""
        paths, errors = [], {}
        for record in self.all():
            if not record["enabled"]:
                continue
            try:
                path, _name, _version = extension_values(record["path"])
                paths.append(path)
            except ValueError as error:
                errors[record["path"]] = str(error)
        return tuple(paths), errors
