"""Own a shared DeepSeek web process for exactly as long as its windows need it."""

import contextlib
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

ICON = Path(__file__).with_name("icons") / "deepseek.svg"
COMMAND = (
    "npx",
    "--yes",
    "--prefer-offline",
    "@deepseek-ai/dsh",
    "web",
    "--no-open",
    "--host",
    "127.0.0.1",
    "--port",
    "0",
)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SECRET = re.compile(
    r"(?i)(\b[\w-]*(?:token|secret|password|passwd|api[_-]?key|authorization|credential)"
    r"""[\w-]*["']?\s*(?:[:=]\s*|\s+))(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
BEARER = re.compile(r"(?i)(\bbearer\s+)\S+")
FAILED = (
    "DeepSeek Harness stopped. Open its tile to try again. "
    "If this keeps happening, run npx @deepseek-ai/dsh web in a terminal to check its setup."
)


def origin(uri):
    try:
        parts = urlsplit(uri)
        return parts.scheme, parts.hostname, parts.port
    except ValueError:
        return None


def startup_uri(line):
    """Accept only the CLI's ready announcement and a concrete loopback URL."""
    line = ANSI.sub("", line).strip()
    if not line.startswith("dsh web: "):
        return None
    uri = line.removeprefix("dsh web: ").strip()
    if len(uri) > 8192 or any(char.isspace() or ord(char) < 32 for char in uri):
        return None
    try:
        parts = urlsplit(uri)
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname in {"127.0.0.1", "::1", "localhost"}
            and parts.port
            and parts.username is None
            and parts.password is None
            and parts.path in {"", "/"}
            and not parts.fragment
        ):
            return uri
    except ValueError:
        pass
    return None


def startup_message(line):
    """Prepare transient startup output for display, without terminal commands or secrets."""
    line = ANSI.sub("", OSC.sub("", line))
    line = "".join(char for char in line if char.isprintable() or char == "\t").strip()

    def redact_url(match):
        try:
            parts = urlsplit(match.group())
            host = parts.netloc.rsplit("@", 1)[-1]
            return f"{parts.scheme}://{host}{parts.path}" + (
                "?[redacted]" if parts.query or parts.fragment else ""
            )
        except ValueError:
            return "[redacted URL]"

    line = URL.sub(redact_url, line)
    line = BEARER.sub(r"\1[redacted]", line)
    return SECRET.sub(r"\1[redacted]", line)[:1000]


def terminate(process):
    """Give the entire session a grace period, then kill surviving descendants."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # npx may exit before Node or a tool it launched. Always clean the group.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


class DeepSeekHarness:
    """Callbacks may run on a worker; dispatch UI work to the main loop.

    Each owner is an opaque window token. Releasing the last owner irrevocably
    cancels the current run. A new acquisition waits for that run's cleanup
    before starting another, including during rapid close/reopen sequences.
    """

    def __init__(self, *, startup_timeout=300):
        self.startup_timeout = startup_timeout
        self._condition = threading.Condition()
        self._clients = {}
        self._pending = set()
        self._origins = set()
        self._uri = None
        self._cancel = None
        self._process = None
        self._thread = None
        self._closed = False
        self._startup_log = deque(maxlen=80)

    def acquire(self, owner, callback):
        with self._condition:
            if self._closed:
                return
            self._clients[owner] = callback
            if self._uri and self._process.poll() is None:
                uri = self._uri
            else:
                uri = None
                self._pending.add(owner)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._worker, daemon=True)
                    self._thread.start()
                self._condition.notify()
        if uri:
            callback(uri, None)

    def release(self, owner):
        with self._condition:
            self._clients.pop(owner, None)
            self._pending.discard(owner)
            if not self._clients and self._cancel:
                self._uri = None
                self._cancel.set()
            if not self._clients:
                self._startup_log.clear()

    def startup_log(self, owner):
        """Return a bounded snapshot only for a window awaiting this startup."""
        with self._condition:
            return "\n".join(self._startup_log) if owner in self._pending else ""

    def _append_log(self, line, cancel):
        message = startup_message(line)
        if message:
            with self._condition:
                if not self._closed and not cancel.is_set():
                    self._startup_log.append(message)

    def owns_uri(self, uri):
        with self._condition:
            return origin(uri) in self._origins

    def close(self):
        with self._condition:
            self._closed = True
            self._clients.clear()
            self._pending.clear()
            self._startup_log.clear()
            if self._cancel:
                self._cancel.set()
            self._condition.notify()
        if self._thread:
            self._thread.join()

    def _worker(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                cancel = self._cancel = threading.Event()
                self._startup_log.clear()
            try:
                self._serve(cancel)
            except (OSError, RuntimeError) as error:
                if not cancel.is_set():
                    # Never forward subprocess output, which can contain credentials.
                    message = str(error) if isinstance(error, HarnessError) else FAILED
                    self._notify(None, message, cancel)
            finally:
                with self._condition:
                    self._uri = self._process = self._cancel = None

    def _notify(self, uri, error, cancel):
        with self._condition:
            if self._closed or cancel.is_set():
                return
            if uri:
                self._uri = uri
                self._origins.add(origin(uri))
                owners = self._pending
            else:
                self._uri = None
                owners = self._clients
            callbacks = [self._clients[owner] for owner in owners]
            self._pending.clear()
            self._startup_log.clear()
        for callback in callbacks:
            callback(uri, error)

    def _serve(self, cancel):
        executable = shutil.which(COMMAND[0])
        if not executable:
            raise HarnessError(
                "Install Node.js and npm so npx is available on your desktop's PATH, "
                "then try again."
            )
        if cancel.is_set():
            return
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("process_supervisor.py")),
                    str(reader),
                    executable,
                    *COMMAND[1:],
                ],
                cwd=Path.home(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                pass_fds=(reader,),
                start_new_session=True,
            )
        except BaseException:
            os.close(writer)
            raise
        finally:
            os.close(reader)
        with self._condition:
            self._process = process
        deadline = time.monotonic() + self.startup_timeout
        pending, discarding, ready = b"", False, False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while not cancel.is_set():
                    if not ready and time.monotonic() >= deadline:
                        raise HarnessError(
                            "DeepSeek Harness took too long to start. Check your network and "
                            "Node.js installation, then open its tile to try again."
                        )
                    for _key, _events in selector.select(timeout=0.1):
                        chunk = os.read(process.stdout.fileno(), 16384)
                        if not chunk:
                            selector.unregister(process.stdout)
                            continue
                        for index, part in enumerate(chunk.replace(b"\r", b"\n").split(b"\n")):
                            if index:
                                if not ready:
                                    line = pending.decode("utf-8", errors="replace")
                                    uri = None if discarding else startup_uri(line)
                                    if uri:
                                        ready = True
                                        self._notify(uri, None, cancel)
                                    else:
                                        self._append_log(
                                            "[Long startup message omitted]"
                                            if discarding
                                            else line,
                                            cancel,
                                        )
                                pending, discarding = b"", False
                            if not ready and not discarding:
                                pending += part
                                if len(pending) > 16384:
                                    pending, discarding = b"", True
                    if process.poll() is not None:
                        raise HarnessError(FAILED)
        finally:
            os.close(writer)
            terminate(process)
            process.stdout.close()


class HarnessError(RuntimeError):
    pass
