"""Private, bounded local transport. This module has no GTK dependency."""

import contextlib
import json
import os
import socket
import stat
import struct
import threading
from pathlib import Path

LIMIT = 12 * 1024 * 1024
TIMEOUT = 20


class ControlError(Exception):
    """An actionable agent command failure, without page or credential contents."""


def socket_path():
    override = os.environ.get("MINI_BROWSER_AGENT_SOCKET")
    if override:
        return Path(override)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "mini-browser-agent" / "control.sock"


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ControlError("Agent socket directory must be owned by you with mode 0700.")


def encode(value):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b"\n"
    if len(data) > LIMIT:
        raise ControlError("Agent message exceeds the 12 MiB limit.")
    return data


def receive(stream):
    data = stream.readline(LIMIT + 1)
    if not data or len(data) > LIMIT or not data.endswith(b"\n"):
        raise ControlError("Missing or oversized agent message.")
    try:
        value = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise ControlError("Invalid agent JSON.") from error
    if not isinstance(value, dict):
        raise ControlError("Agent message must be a JSON object.")
    return value


def check_peer(connection):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    if struct.unpack("3i", credentials)[1] != os.getuid():
        raise ControlError("Agent connections require the same local user account.")


def request(method, parameters=None, *, path=None):
    target = Path(path or socket_path())
    try:
        private_directory(target.parent)
        info = target.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ControlError("Agent socket must be owned by you with mode 0600.")
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(TIMEOUT)
            connection.connect(str(target))
            check_peer(connection)
            connection.sendall(encode({"method": method, "params": parameters or {}}))
            with connection.makefile("rb") as stream:
                reply = receive(stream)
    except FileNotFoundError as error:
        raise ControlError(
            "Agent control is unavailable. Start Mini Browser with --agent."
        ) from error
    except OSError as error:
        raise ControlError(
            "Cannot reach agent control. Restart Mini Browser with --agent."
        ) from error
    if "error" in reply:
        raise ControlError(reply["error"])
    if "result" not in reply:
        raise ControlError("Invalid agent response.")
    return reply["result"]


class ControlServer:
    """Socket work is off-thread; dispatch schedules all application work on GTK."""

    def __init__(self, dispatch, *, path=None):
        self.path = Path(path or socket_path())
        private_directory(self.path.parent)
        self.dispatch = dispatch
        self.stopped = threading.Event()
        self.connection_lock = threading.Lock()
        self.connections = set()
        self.slots = threading.BoundedSemaphore(8)
        self.listener = socket.socket(socket.AF_UNIX)
        self.identity = None
        try:
            self._remove_stale()
            self.listener.bind(str(self.path))
            self.identity = self.path.stat().st_ino
            self.path.chmod(0o600)
            self.listener.listen(8)
            self.listener.settimeout(0.2)
        except BaseException:
            self.listener.close()
            if self.identity is not None:
                with contextlib.suppress(FileNotFoundError):
                    if self.path.lstat().st_ino == self.identity:
                        self.path.unlink()
            raise
        self.thread = threading.Thread(target=self._accept, daemon=True, name="agent-control")
        self.thread.start()

    def _remove_stale(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ControlError("Refusing to replace a non-socket agent path.")
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(str(self.path))
            except ConnectionRefusedError:
                self.path.unlink()
            else:
                raise ControlError("Agent control is already running at this socket.")

    def _accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            with self.connection_lock:
                self.connections.add(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        try:
            connection.settimeout(TIMEOUT)
            check_peer(connection)
            with connection.makefile("rb") as stream:
                message = receive(stream)
            method, params = message.get("method"), message.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ControlError("Expected a method string and params object.")
            done = threading.Event()
            reply_lock = threading.Lock()
            reply = {}

            def finish(result=None, error=None):
                with reply_lock:
                    if done.is_set():
                        return
                    reply.update({"error": str(error)} if error else {"result": result})
                    done.set()

            finish.cancelled = done
            self.dispatch(method, params, finish, done)
            if not done.wait(TIMEOUT - 1):
                finish(error="Agent command timed out; its outcome may be unknown.")
            connection.sendall(encode(reply))
        except (ControlError, ValueError, RecursionError) as error:
            with contextlib.suppress(OSError):
                connection.sendall(encode({"error": str(error)}))
        except OSError:
            pass
        finally:
            with self.connection_lock:
                self.connections.discard(connection)
            connection.close()
            self.slots.release()

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.thread.join(timeout=1)
        with self.connection_lock:
            for connection in self.connections:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(FileNotFoundError):
            if self.path.lstat().st_ino == self.identity:
                self.path.unlink()
