"""Download, authenticate and stage catalog extensions without loading GTK."""

import base64
import hashlib
import http.client
import json
import re
import shutil
import stat
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path
from sqlite3 import Error as DatabaseError

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from .extensions import MAX_MANIFEST_BYTES, extension_ui, extension_values

MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_HEADER = 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
MAX_FILES = 20000
CHUNK = 128 * 1024
SIGNATURE_PREFIX = b"CRX3 SignedData\0"
DOWNLOAD_HOSTS = {"clients2.google.com", "clients2.googleusercontent.com"}


class InstallError(ValueError):
    pass


def _identifier(raw):
    return raw.hex().translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))


def _check_cancel(cancelled):
    if cancelled():
        raise InstallError("Installation cancelled.")


def package_url(identifier, browser_version):
    if not re.fullmatch(r"[a-p]{32}", identifier):
        raise InstallError("The catalog extension ID is invalid.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", browser_version):
        raise InstallError("The Chromium version could not be identified.")
    query = urllib.parse.urlencode(
        {
            "response": "redirect",
            "prodversion": browser_version,
            "acceptformat": "crx3",
            "x": f"id={identifier}&uc",
        }
    )
    return "https://clients2.google.com/service/update2/crx?" + query


def _check_download_url(url):
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in DOWNLOAD_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
    ):
        raise InstallError("The extension download was redirected to an unexpected source.")


class _StoreRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        _check_download_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def download_package(url, destination, progress, cancelled):
    _check_download_url(url)
    opener = urllib.request.build_opener(_StoreRedirect())
    deadline, last_progress, received = time.monotonic() + 120, 0, 0
    request = urllib.request.Request(url, headers={"User-Agent": "MiniBrowser/0.4"})
    try:
        with opener.open(request, timeout=15) as response, destination.open("xb") as output:
            _check_download_url(response.geturl())
            try:
                total = max(0, int(response.headers.get("Content-Length", 0)))
            except ValueError:
                total = 0
            if total > MAX_DOWNLOAD:
                raise InstallError("The extension download is too large (maximum 100 MiB).")
            while True:
                _check_cancel(cancelled)
                if time.monotonic() > deadline:
                    raise InstallError("The extension download timed out. Try again.")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD:
                    raise InstallError("The extension download is too large (maximum 100 MiB).")
                output.write(chunk)
                if time.monotonic() - last_progress >= 0.25:
                    progress(
                        f"Downloading {min(100, received * 100 // total)}%"
                        if total
                        else "Downloading…"
                    )
                    last_progress = time.monotonic()
            if not received or total > 0 and received != total:
                raise InstallError("The extension download was incomplete. Try again.")
    except urllib.error.HTTPError as error:
        raise InstallError(
            f"The extension store returned HTTP {error.code}. Try again later."
        ) from error
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as error:
        raise InstallError(
            "Could not download the extension. Check your connection and try again."
        ) from error


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    raise InstallError("The extension package header is invalid.")


def _fields(data):
    """Read the bounded length-delimited CRX3 protobuf fields, without a runtime dependency."""
    offset = 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < 2**29:
            raise InstallError("The extension package header is invalid.")
        if wire == 2:
            length, offset = _varint(data, offset)
            end = offset + length
            if end > len(data):
                raise InstallError("The extension package header is truncated.")
            yield number, data[offset:end]
            offset = end
        elif wire == 0:
            _value, offset = _varint(data, offset)
        elif wire in (1, 5):
            offset += 8 if wire == 1 else 4
            if offset > len(data):
                raise InstallError("The extension package header is truncated.")
        else:
            raise InstallError("The extension package header is invalid.")


def _single(fields, number):
    values = [value for field, value in fields if field == number]
    if len(values) != 1:
        raise InstallError("The extension package is missing its signed identity.")
    return values[0]


def verify_package(package, expected_id, cancelled=lambda: False):
    """Verify the publisher-key proof over CRX3 SignedData and the complete ZIP payload."""
    with package.open("rb") as stream:
        prefix = stream.read(12)
        if len(prefix) != 12:
            raise InstallError("The downloaded file is not a signed CRX3 extension.")
        magic, version, length = struct.unpack("<4sII", prefix)
        if magic != b"Cr24" or version != 3 or not 0 < length <= MAX_HEADER:
            raise InstallError("The downloaded file is not a supported signed CRX3 extension.")
        header = stream.read(length)
        if len(header) != length:
            raise InstallError("The extension package header is truncated.")
        fields = list(_fields(header))
        signed = _single(fields, 10000)
        crx_id = _single(list(_fields(signed)), 1)
        if len(crx_id) != 16 or _identifier(crx_id) != expected_id:
            raise InstallError("The downloaded extension does not match this Market entry.")
        digest = hashes.Hash(hashes.SHA256())
        digest.update(SIGNATURE_PREFIX + struct.pack("<I", len(signed)) + signed)
        size = 12 + length
        while chunk := stream.read(CHUNK):
            _check_cancel(cancelled)
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise InstallError("The extension package is too large.")
            digest.update(chunk)
        hashed = digest.finalize()
    for algorithm, proof in fields:
        if algorithm not in (2, 3):
            continue
        parts = list(_fields(proof))
        public_bytes, signature = _single(parts, 1), _single(parts, 2)
        if _identifier(hashlib.sha256(public_bytes).digest()[:16]) != expected_id:
            continue
        try:
            key = serialization.load_der_public_key(public_bytes)
            if algorithm == 2 and isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048:
                key.verify(signature, hashed, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
            elif algorithm == 3 and isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(signature, hashed, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
            else:
                continue
            return public_bytes
        except (InvalidSignature, UnsupportedAlgorithm, ValueError, TypeError):
            continue
    raise InstallError("The extension signature could not be verified. Nothing was installed.")


def unpack_package(package, folder, cancelled=lambda: False):
    try:
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_UNPACKED:
                raise InstallError("The extension archive exceeds the installation limits.")
            seen = set()
            for item in entries:
                _check_cancel(cancelled)
                name = item.filename
                parts = name.rstrip("/").split("/")
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (
                    not name
                    or name.startswith("/")
                    or "\\" in name
                    or ".." in parts
                    or any(not part or ":" in part for part in parts)
                    or any(ord(char) < 32 for char in name)
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or item.flag_bits & 1
                ):
                    raise InstallError(
                        "The extension archive contains an unsafe file path or type."
                    )
                if name in seen:
                    raise InstallError("The extension archive contains duplicate files.")
                seen.add(name)
                target = folder.joinpath(*parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(item) as source, target.open("xb") as output:
                        while chunk := source.read(CHUNK):
                            _check_cancel(cancelled)
                            output.write(chunk)
                    target.chmod(0o600)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as error:
        raise InstallError("The extension archive is corrupt or unsupported.") from error


class ExtensionInstaller:
    def __init__(self, store, directory, browser_version):
        self.store = store
        self.directory = Path(directory)
        self.browser_version = browser_version

    def install(self, entry, progress=lambda _message: None, cancelled=lambda: False):
        identifier = entry["id"]
        url = package_url(identifier, self.browser_version)
        previous = self.store.market_installs().get(identifier)
        if previous:
            try:
                if extension_ui(previous)["id"] == identifier:
                    return previous
            except ValueError:
                pass
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if "," in str(self.directory.resolve()):
            raise InstallError(
                "The browser data folder cannot contain commas when loading extensions."
            )
        destination = self.directory / identifier / uuid.uuid4().hex
        promoted = False
        try:
            with tempfile.TemporaryDirectory(prefix=".install-", dir=self.directory) as temporary:
                staging = Path(temporary)
                package, content = staging / "source.crx", staging / "extension"
                _check_cancel(cancelled)
                progress("Downloading…")
                download_package(url, package, progress, cancelled)
                progress("Verifying signature…")
                public_key = verify_package(package, identifier, cancelled)
                content.mkdir(mode=0o700)
                progress("Installing…")
                unpack_package(package, content, cancelled)
                manifest_path = content / "manifest.json"
                with manifest_path.open("rb") as source:
                    raw = source.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise InstallError("The extension manifest is too large.")
                try:
                    manifest = json.loads(raw)
                except (ValueError, RecursionError) as error:
                    raise InstallError("The downloaded extension manifest is invalid.") from error
                if not isinstance(manifest, dict) or manifest.get("manifest_version") != 3:
                    raise InstallError("Only Manifest V3 extensions can be installed.")
                manifest["key"] = base64.b64encode(public_key).decode("ascii")
                if str(manifest.get("name", "")).startswith("__MSG_"):
                    manifest["name"] = entry["name"]
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                manifest_path.chmod(0o600)
                extension_values(content)
                _check_cancel(cancelled)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                content.rename(destination)
                promoted = True
                # Retain the original signed source outside the extension's resource directory.
                package.rename(destination.with_suffix(".crx"))
                return self.store.register_market(identifier, destination)
        except (ValueError, OSError, DatabaseError, zipfile.BadZipFile):
            if promoted:
                shutil.rmtree(destination, ignore_errors=True)
                destination.with_suffix(".crx").unlink(missing_ok=True)
            raise
