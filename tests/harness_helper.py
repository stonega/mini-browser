#!/usr/bin/python3
"""Synthetic npx and server for lifecycle tests; never invoke npm or DeepSeek."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def fake_harness(root, **config):
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "npx"
    shutil.copyfile(__file__, executable)
    executable.chmod(0o755)
    (root / "config.json").write_text(json.dumps(config))
    return {"PATH": str(root) + os.pathsep + os.environ.get("PATH", "")}


def runs(root):
    return [json.loads(path.read_text()) for path in sorted(root.glob("run-*.json"))]


def running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in {"Z", "X"}
    except (FileNotFoundError, ProcessLookupError):
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).query:
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "fixture=authorized; HttpOnly; SameSite=Strict")
            self.end_headers()
            return
        page = b"<title>Harness fixture</title><h1>Synthetic DeepSeek Harness</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *_):
        pass


def main():
    root = Path(__file__).parent
    if sys.argv[1:] == ["--child"]:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / f"child-{os.getpid()}").touch()
        while True:
            time.sleep(1)
    config = json.loads((root / "config.json").read_text())
    record = {"pid": os.getpid(), "group": os.getpgrp(), "args": sys.argv[1:], "cwd": os.getcwd()}
    temporary = root / f"run-{os.getpid()}.tmp"
    temporary.write_text(json.dumps(record))
    temporary.replace(root / f"run-{os.getpid()}.json")
    if config.get("child"):
        subprocess.Popen([sys.executable, __file__, "--child"])
    for message in config.get("messages", []):
        print(message, flush=True)
    if config.get("mode") == "fail":
        print("Synthetic failure with private-fixture-token", file=sys.stderr, flush=True)
        return 1
    time.sleep(config.get("delay", 0))
    if config.get("mode") == "silent":
        while True:
            time.sleep(1)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    # Include a long diagnostic, unrelated URL and split, colored readiness line.
    print("x" * 50000, flush=True)
    print("Download: https://example.invalid/package", flush=True)
    os.write(1, b"\x1b[32mdsh we")
    time.sleep(0.02)
    print(
        f"b: http://127.0.0.1:{server.server_port}/?token=private-fixture-token\x1b[0m", flush=True
    )
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
