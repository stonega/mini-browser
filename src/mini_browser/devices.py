"""Saved phones and mirroring transports. This module does not depend on GTK."""

import base64
import contextlib
import os
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from . import scrcpy_bundle

KINDS = {"android": "Android", "iphone": "iPhone"}
SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,255}\Z")


class DeviceError(Exception):
    """An actionable message that contains no helper output or phone contents."""


@dataclass(frozen=True)
class Device:
    id: str
    kind: str
    name: str
    address: str = ""
    identity: str = ""

    @property
    def receiver_name(self):
        label = self.name.encode("utf-8")[:32].decode("utf-8", errors="ignore")
        return f"Mini Browser {label} {self.id[:8]}"


def android_device_id(address):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"mini-browser:android:{address}").hex


class AndroidConnection(NamedTuple):
    address: str
    name: str
    state: str
    identity: str = ""
    usb: bool = False


def read_android_identity(command, adb, address):
    """Read only hardware identity, never screen contents or user data."""
    if address.startswith("emulator-"):
        return ""
    for prop in ("ro.serialno", "ro.boot.serialno"):
        try:
            value = command([adb, "-s", address, "shell", "getprop", prop], timeout=2).strip()
        except (DeviceError, OSError):
            continue
        if SERIAL.fullmatch(value) and value.casefold() not in {"unknown", "null", "none", "0"}:
            return value
    return ""


def validate_device(kind, name, address=""):
    name = name.strip()
    address = address.strip()
    if kind not in KINDS:
        raise ValueError("Choose Android or iPhone.")
    if not name or len(name) > 48 or not name.isprintable():
        raise ValueError("Use a phone name containing 1–48 characters without control characters.")
    if kind == "android" and not SERIAL.fullmatch(address):
        raise ValueError("Enter an Android device serial or an already paired ADB address.")
    return name, address if kind == "android" else ""


class DeviceStore:
    def __init__(self, library):
        self.library = library
        with library.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                name TEXT NOT NULL, address TEXT NOT NULL, identity TEXT NOT NULL DEFAULT ''
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(devices)")}
            if "identity" not in columns:
                db.execute("ALTER TABLE devices ADD COLUMN identity TEXT NOT NULL DEFAULT ''")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS android_device_address
                ON devices(address) WHERE kind = 'android'""")

    def all(self):
        with self.library.connect() as db:
            return [
                Device(**dict(row)) for row in db.execute("SELECT * FROM devices ORDER BY rowid")
            ]

    def save(self, kind, name, address="", *, device_id=None, identity=""):
        name, address = validate_device(kind, name, address)
        identity = identity if kind == "android" else ""
        if identity and not SERIAL.fullmatch(identity):
            raise ValueError(
                "The phone's hardware identity is invalid. Reconnect it and try again."
            )
        with self.library.connect() as db:
            if device_id is not None:
                current = db.execute(
                    "SELECT kind FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
                if current is None or current["kind"] != kind:
                    raise ValueError("This phone is no longer saved. Close Settings and try again.")
                # Editing a name must not silently change which phone receives input.
                db.execute(
                    "UPDATE devices SET name = ?, identity = CASE WHEN identity = '' "
                    "THEN ? ELSE identity END WHERE id = ?",
                    (name, identity, device_id),
                )
            else:
                current = None
                if identity:
                    current = db.execute(
                        "SELECT * FROM devices WHERE kind = 'android' AND identity = ? "
                        "ORDER BY rowid",
                        (identity,),
                    ).fetchone()
                if current is None and kind == "android":
                    current = db.execute(
                        "SELECT * FROM devices WHERE kind = 'android' AND address = ?", (address,)
                    ).fetchone()
                    if current and identity and current["identity"] not in {"", identity}:
                        raise ValueError(
                            "This address belongs to another saved phone. "
                            "Remove its saved entry first."
                        )
                if current:
                    device_id = current["id"]
                elif kind == "android":
                    device_id = android_device_id(identity or address)
                else:
                    device_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO devices (id, kind, name, address, identity) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, identity = "
                    "CASE WHEN devices.identity = '' "
                    "THEN excluded.identity ELSE devices.identity END",
                    (device_id, kind, name, address, identity),
                )
            row = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return Device(**dict(row))

    def bind_android_identities(self, connections):
        with self.library.connect() as db:
            for connection in connections:
                if connection.identity:
                    db.execute(
                        "UPDATE devices SET identity = ? WHERE kind = 'android' "
                        "AND address = ? AND identity = ''",
                        (connection.identity, connection.address),
                    )

    def remove(self, device_id):
        with self.library.connect() as db:
            db.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def android_tools():
    adb = shutil.which("adb")
    if not adb:
        raise DeviceError("Install adb (Android platform tools), then reconnect.")
    try:
        server = scrcpy_bundle.server_path()
    except (OSError, ValueError):
        raise DeviceError(
            "The bundled Android helper is missing or damaged. Reinstall Mini Browser."
        ) from None
    return adb, server, scrcpy_bundle.VERSION


def parse_android_devices(output):
    devices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or not SERIAL.fullmatch(fields[0]):
            continue
        if fields[1] not in {"device", "unauthorized", "offline"}:
            continue
        model = next((field[6:] for field in fields[2:] if field.startswith("model:")), "Android")
        devices.append((fields[0], model.replace("_", " "), fields[1]))
    return devices


def discover_android():
    from .device_discovery import AndroidDiscovery, preferred_connections

    adb = shutil.which("adb")
    if not adb:
        raise DeviceError("Install adb (Android platform tools), then find phones again.")
    scanner = AndroidDiscovery(lambda *_: None)
    try:
        return [tuple(connection[:3]) for connection in preferred_connections(scanner.scan(adb))]
    finally:
        scanner.stop()


def phone_coordinates(x, y, viewport, frame, native):
    """Map a point in a letterboxed picture; ignore clicks outside the phone."""
    width, height = viewport
    fw, fh = frame
    nw, nh = native
    if min(width, height, fw, fh, nw, nh) <= 0:
        return None
    scale = min(width / fw, height / fh)
    left, top = (width - fw * scale) / 2, (height - fh * scale) / 2
    if not (left <= x < width - left and top <= y < height - top):
        return None
    if (fw > fh) != (nw > nh):
        nw, nh = nh, nw
    return min(nw - 1, int((x - left) / (fw * scale) * nw)), min(
        nh - 1, int((y - top) / (fh * scale) * nh)
    )


class Transport:
    """Own helper processes and bound shutdown; never inherit a terminal or log output."""

    def __init__(self, status, finished):
        self.status = status
        self.finished = finished
        self.cancelled = threading.Event()
        self.stopped = threading.Event()
        self._previous = ()
        self._lock = threading.Lock()
        self._processes = set()
        self.thread = None

    def spawn(self, arguments, *, capture=False):
        with self._lock:
            if self.cancelled.is_set():
                raise DeviceError("Connection stopped.")
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._processes.add(process)
        return process

    def command(self, arguments, timeout=10):
        process = self.spawn(arguments, capture=True)
        try:
            output, _ = process.communicate(timeout=timeout)
            if process.returncode:
                raise DeviceError(
                    "The phone did not respond. Check its connection and authorization."
                )
            return output.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise DeviceError(
                "The phone timed out. Unlock it, check the connection, and reconnect."
            ) from exc
        finally:
            with self._lock:
                self._processes.discard(process)

    def start(self, *, after=()):
        self._previous = tuple(after)
        self.thread = threading.Thread(target=self._worker, name="phone-mirror", daemon=True)
        self.thread.start()

    def _worker(self):
        try:
            for previous in self._previous:
                while not previous.stopped.wait(0.1):
                    if self.cancelled.is_set():
                        return
            if self.cancelled.is_set():
                return
            self._previous = ()
            self.run()
        except (DeviceError, OSError) as exc:
            if not self.cancelled.is_set():
                self.status(
                    "error",
                    str(exc) if isinstance(exc, DeviceError) else "Could not start mirroring.",
                )
        finally:
            self.stop()
            with self._lock:
                processes = tuple(self._processes)
            for process in processes:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            try:
                self.cleanup()
            finally:
                self._previous = ()
                self.stopped.set()
                self.finished()

    def stop(self):
        self.cancelled.set()
        with self._lock:
            for process in self._processes:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)

    def cleanup(self):
        pass


class AndroidTransport(Transport):
    def __init__(self, device, feed, status, size, finished):
        super().__init__(status, finished)
        self.device = device
        self.feed = feed
        self.size = size
        self.connection = None
        self.adb = None
        self.forward = None
        self.remote = None
        self.inputs = queue.Queue(maxsize=32)
        self.input_thread = None

    def adb_command(self, *arguments):
        return [self.adb, "-s", self.device.address, *arguments]

    def run(self):
        self.adb, server, version = android_tools()
        try:
            state = self.command(self.adb_command("get-state")).strip()
        except DeviceError:
            raise DeviceError(
                "Unlock the Android phone, check the connection, and approve USB debugging."
            ) from None
        if state != "device":
            raise DeviceError(
                "Unlock the Android phone and approve this computer's debugging request."
            )
        if self.device.identity:
            actual = read_android_identity(self.command, self.adb, self.device.address)
            if actual != self.device.identity:
                raise DeviceError(
                    "This connection no longer identifies this phone. "
                    "Choose its connected tile in Devices."
                )
        sizes = re.findall(
            r"(?:Physical|Override) size:\s*(\d+)x(\d+)",
            self.command(self.adb_command("shell", "wm", "size")),
        )
        if not sizes:
            raise DeviceError("Could not read the Android display size. Reconnect the phone.")
        self.size(tuple(map(int, sizes[-1])))
        scid = secrets.randbelow(0x7FFFFFFF)
        self.remote = f"/data/local/tmp/mini-browser-{scid:08x}.jar"
        self.command(self.adb_command("push", str(server), self.remote), timeout=20)
        self.forward = self.command(
            self.adb_command("forward", "tcp:0", f"localabstract:scrcpy_{scid:08x}")
        ).strip()
        if not self.forward.isdecimal() or not 0 < int(self.forward) < 65536:
            raise DeviceError(
                "ADB could not open a private mirroring connection. Reconnect the phone."
            )
        process = self.spawn(
            self.adb_command(
                "shell",
                f"CLASSPATH={self.remote}",
                "app_process",
                "/",
                "com.genymobile.scrcpy.Server",
                version,
                f"scid={scid:08x}",
                "log_level=error",
                "audio=false",
                "control=false",
                "max_size=1280",
                "max_fps=30",
                "tunnel_forward=true",
                "raw_stream=true",
                "cleanup=true",
            )
        )
        deadline = time.monotonic() + 15
        while not self.cancelled.is_set():
            try:
                connection = socket.create_connection(("127.0.0.1", int(self.forward)), timeout=0.5)
                # ADB may accept the local socket before the device server is ready.
                first = b""
                while not self.cancelled.is_set() and time.monotonic() < deadline:
                    try:
                        first = connection.recv(65536)
                        break
                    except TimeoutError:
                        if process.poll() is not None:
                            break
                if first:
                    self.connection = connection
                    self.feed(first)
                    break
                connection.close()
            except OSError:
                if "connection" in locals():
                    connection.close()
            if process.poll() is not None or time.monotonic() > deadline:
                raise DeviceError(
                    "Could not start the phone's display. Unlock it, check USB debugging "
                    "permissions, and reconnect."
                )
            self.cancelled.wait(0.1)
        if self.cancelled.is_set():
            return
        self.input_thread = threading.Thread(
            target=self._input_worker, name="phone-input", daemon=True
        )
        self.input_thread.start()
        while not self.cancelled.is_set():
            try:
                data = self.connection.recv(65536)
            except TimeoutError:
                continue
            if not data:
                break
            self.feed(data)
        if not self.cancelled.is_set():
            self.status("disconnected", "Phone disconnected. Check its connection, then reconnect.")

    def send(self, *arguments):
        if not self.cancelled.is_set():
            with contextlib.suppress(queue.Full):
                self.inputs.put_nowait(tuple(arguments))

    def _input_worker(self):
        while not self.cancelled.is_set():
            try:
                arguments = self.inputs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                # adb shell joins its arguments into a remote shell command. Quote each one.
                self.command(
                    self.adb_command("shell", "input", *(shlex.quote(x) for x in arguments))
                )
            except (DeviceError, OSError):
                if not self.cancelled.is_set():
                    self.status("input-error", "Phone input failed. Check debugging permissions.")

    def stop(self):
        super().stop()
        if self.connection:
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

    def cleanup(self):
        if self.input_thread:
            self.input_thread.join(timeout=3)
        if self.adb:
            for arguments in (
                ("forward", "--remove", f"tcp:{self.forward}") if self.forward else (),
                ("shell", "rm", "-f", self.remote) if self.remote else (),
            ):
                if arguments:
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        subprocess.run(
                            self.adb_command(*arguments),
                            timeout=3,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )


def iphone_arguments(executable, device, directory, port, pin):
    """Use private configuration and a stable receiver identity, scoped to this tile."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    register, config = directory / "clients", directory / "uxplay.conf"
    for path in (register, config):
        with path.open("a", encoding="utf-8"):
            path.chmod(0o600)
    try:
        records = [
            line.split(",", 2)
            for line in register.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(records) > 1 or any(
            len(row) != 3
            or not SERIAL.fullmatch(row[1])
            or len(base64.b64decode(row[0], validate=True)) != 32
            for row in records
        ):
            raise ValueError
    except ValueError:
        raise DeviceError(
            "Phone pairing data is invalid. Remove this phone in Settings and add it again."
        ) from None
    mac = "02:" + ":".join(device.id[index : index + 2] for index in range(0, 10, 2))
    arguments = [
        executable,
        "-rc",
        str(config),
        "-n",
        device.receiver_name,
        "-nh",
        "-m",
        mac,
        "-pin",
        pin,
        "-reg",
        str(register),
        "-a",
        "-nofreeze",
        "-s",
        "1280x720",
        "-vrtp",
        f"config-interval=1 ! udpsink host=127.0.0.1 port={port} sync=false",
    ]
    if records:
        arguments.extend(["-restrict", "-allow", records[0][1]])
    return arguments


class IPhoneTransport(Transport):
    def __init__(self, device, directory, port, pin, status, finished):
        super().__init__(status, finished)
        self.device, self.directory, self.port, self.pin = device, directory, port, pin

    def run(self):
        executable = shutil.which("uxplay")
        if not executable:
            raise DeviceError("Install UxPlay 1.73 or newer, then reconnect.")
        help_text = self.command([executable, "-h"])
        if "-vrtp" not in help_text:
            raise DeviceError("This UxPlay is too old. Install UxPlay 1.73 or newer.")
        process = self.spawn(
            iphone_arguments(executable, self.device, self.directory, self.port, self.pin)
        )
        self.status("waiting", "Start Screen Mirroring from Control Center on your iPhone.")
        while not self.cancelled.wait(0.25):
            if process.poll() is not None:
                raise DeviceError(
                    "The AirPlay receiver stopped. Check UxPlay, GStreamer, local network access, "
                    "and Avahi service discovery, then reconnect."
                )
