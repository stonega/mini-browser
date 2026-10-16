"""App-local encrypted password vault. No dependency on a desktop keyring.

Version 1: magic (8), Argon2id salt (16), AES-GCM nonce (12), ciphertext/tag.
Only this fixed version's parameters are accepted; the whole record set is
encrypted. Atomic replacements and revision checks prevent partial/lost writes.
"""

import csv
import fcntl
import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .navigation import is_web_url

MAGIC = b"MINIVLT1"
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 10_000
LOCK_AFTER = 5 * 60


class VaultError(Exception):
    """An actionable error with no credential values in its message."""


@dataclass(frozen=True)
class Credential:
    origin: str
    username: str
    password: str = field(repr=False)
    name: str = ""


@dataclass(frozen=True)
class PasswordImportResult:
    added: int
    updated: int
    skipped: int


def credential_origin(url: str) -> str:
    if not is_web_url(url) or any(
        ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise VaultError("Enter a valid http:// or https:// website address.")
    parts = urlsplit(url)
    host = parts.hostname
    # Chrome exports canonical ASCII/punycode URLs. Do not perform IDNA2003
    # mappings (e.g. ß -> ss) that could bind a credential to a different site.
    if not host.isascii() or parts.username is not None or parts.password is not None:
        raise VaultError("Use the website address copied from the browser.")
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    suffix = f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    return f"{parts.scheme}://{host}{suffix}"


def can_fill_origin(origin: str) -> bool:
    """HTTPS or loopback HTTP only; never upgrade an imported HTTP credential."""
    try:
        parts = urlsplit(credential_origin(origin))
        return parts.scheme == "https" or parts.hostname in {"localhost", "127.0.0.1", "::1"}
    except VaultError:
        return False


def read_password_csv(path: Path) -> tuple[list[Credential], int]:
    try:
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise VaultError("The password CSV is too large (maximum 16 MiB).")
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        if not {"url", "username", "password"}.issubset(reader.fieldnames or []):
            raise VaultError("Choose a Chrome CSV with url, username, and password columns.")
        records, skipped = {}, 0
        for index, row in enumerate(reader):
            if index >= MAX_RECORDS:
                raise VaultError("Import at most 10,000 CSV rows at a time.")
            try:
                origin = credential_origin(row.get("url") or "")
                username, password = row.get("username"), row.get("password")
                name = row.get("name") or origin
                if (
                    username is None
                    or not password
                    or None in row
                    or len(username) > 4096
                    or len(password) > 65536
                    or len(name) > 512
                    or "\x00" in name
                    or "\x00" in username
                    or "\x00" in password
                ):
                    raise VaultError("Invalid CSV row")
                records[(origin, username)] = Credential(origin, username, password, name)
            except VaultError:
                skipped += 1
        if not records:
            raise VaultError("No valid website passwords were found in this CSV.")
        return list(records.values()), skipped
    except (OSError, UnicodeError, csv.Error):
        raise VaultError("Could not read the password CSV. Export it again from Chrome.") from None


class PasswordVault:
    def __init__(self, path: Path):
        self.path = path
        self._mutex = threading.RLock()
        self._key = None
        self._salt = None
        self._records = {}
        self._revision = None
        self._last_used = 0

    @property
    def exists(self):
        return self.path.exists()

    @property
    def unlocked(self):
        with self._mutex:
            self.expire()
            return self._key is not None

    def lock(self):
        with self._mutex:
            if self._key is not None:
                self._key[:] = b"\x00" * len(self._key)
            self._key = self._salt = self._revision = None
            self._records = {}
            self._last_used = 0

    def expire(self):
        with self._mutex:
            if self._key is not None and time.monotonic() - self._last_used >= LOCK_AFTER:
                self.lock()
                return True
            return False

    def _require_unlocked(self):
        if not self.unlocked:
            raise VaultError("Unlock the password vault first.")
        self._last_used = time.monotonic()

    @staticmethod
    def _derive(password, salt):
        return Argon2id(
            salt=salt,
            length=32,
            iterations=3,
            lanes=4,
            memory_cost=64 * 1024,
        ).derive(password.encode("utf-8"))

    @staticmethod
    def _check_new_pin(password):
        if len(password) != 6 or not password.isascii() or not password.isdigit():
            raise VaultError("Use exactly 6 digits (0–9) for the vault PIN.")

    def _read(self):
        try:
            with self.path.open("rb") as source:
                data = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_BYTES:
            raise VaultError("The vault file is too large or damaged.")
        return data

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _commit(self, key, salt, records, revision):
        payload = json.dumps(
            [
                {
                    "origin": item.origin,
                    "username": item.username,
                    "password": item.password,
                    "name": item.name,
                }
                for item in records.values()
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        header = MAGIC + salt
        data = header + nonce + AESGCM(bytes(key)).encrypt(nonce, payload, header)
        if len(data) > MAX_BYTES:
            raise VaultError("The vault is full (maximum 16 MiB).")
        with self._file_lock():
            current = self._read()
            actual = hashlib.sha256(current).digest() if current is not None else None
            if actual != revision:
                self.lock()
                raise VaultError("The vault changed in another process. Unlock it again.")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, prefix=".vault-", delete=False
                ) as f:
                    temporary = Path(f.name)
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, self.path)
                temporary = None
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return hashlib.sha256(data).digest()

    def create(self, password):
        with self._mutex:
            self._check_new_pin(password)
            salt = os.urandom(16)
            key = bytearray(self._derive(password, salt))
            try:
                revision = self._commit(key, salt, {}, None)
                self.lock()
                self._key, self._salt, self._records, self._revision = key, salt, {}, revision
                self._last_used = time.monotonic()
            except Exception:
                key[:] = b"\x00" * len(key)
                raise

    def unlock(self, password):
        with self._mutex:
            self.lock()
            data = self._read()
            if data is None:
                raise VaultError("Create a password vault first.")
            if len(data) < 52 or data[:8] != MAGIC:
                raise VaultError("This vault format is unsupported or the file is damaged.")
            salt, nonce = data[8:24], data[24:36]
            key = bytearray(self._derive(password, salt))
            try:
                payload = AESGCM(bytes(key)).decrypt(nonce, data[36:], data[:24])
                values = json.loads(payload)
                if not isinstance(values, list) or len(values) > MAX_RECORDS:
                    raise ValueError()
                records = {}
                for value in values:
                    item = Credential(**value)
                    if (
                        not all(
                            isinstance(v, str)
                            for v in (
                                item.origin,
                                item.username,
                                item.password,
                                item.name,
                            )
                        )
                        or credential_origin(item.origin) != item.origin
                    ):
                        raise ValueError()
                    records[(item.origin, item.username)] = item
                self._key, self._salt, self._records = key, salt, records
                self._revision = hashlib.sha256(data).digest()
                self._last_used = time.monotonic()
            except (InvalidTag, ValueError, TypeError, UnicodeError, VaultError):
                key[:] = b"\x00" * len(key)
                raise VaultError(
                    "Could not unlock. Check the PIN or existing master password."
                ) from None

    def import_csv(self, path: Path) -> PasswordImportResult:
        # Do not keep a CSV waiting in memory while the vault is locked.
        with self._mutex:
            self._require_unlocked()
            entries, skipped = read_password_csv(path)
            records = dict(self._records)
            added = updated = 0
            for entry in entries:
                identity = (entry.origin, entry.username)
                if identity not in records:
                    added += 1
                elif records[identity] != entry:
                    updated += 1
                records[identity] = entry
            if len(records) > MAX_RECORDS:
                raise VaultError("The vault holds at most 10,000 saved logins.")
            revision = self._commit(self._key, self._salt, records, self._revision)
            self._records, self._revision = records, revision
            return PasswordImportResult(added, updated, skipped)

    def summaries(self, origin=None):
        with self._mutex:
            self._require_unlocked()
            return sorted(
                [
                    {"origin": item.origin, "username": item.username, "name": item.name}
                    for item in self._records.values()
                    if origin is None or item.origin == origin
                ],
                key=lambda item: (item["origin"], item["username"]),
            )

    def credential(self, origin, username):
        with self._mutex:
            self._require_unlocked()
            item = self._records.get((origin, username))
            if item is None:
                raise VaultError("This saved login is no longer available.")
            return item

    def delete(self, origin, username):
        with self._mutex:
            self._require_unlocked()
            records = dict(self._records)
            records.pop((origin, username), None)
            revision = self._commit(self._key, self._salt, records, self._revision)
            self._records, self._revision = records, revision

    def change_password(self, current, new):
        with self._mutex:
            self._require_unlocked()
            self._check_new_pin(new)
            if not hmac.compare_digest(self._derive(current, self._salt), bytes(self._key)):
                raise VaultError("The current PIN or master password is incorrect.")
            salt = os.urandom(16)
            key = bytearray(self._derive(new, salt))
            try:
                revision = self._commit(key, salt, self._records, self._revision)
                self._key[:] = b"\x00" * len(self._key)
                self._key, self._salt, self._revision = key, salt, revision
            except Exception:
                key[:] = b"\x00" * len(key)
                raise
