"""Public CLI/MCP control of real GTK/Chromium, on the disposable GNOME desktop."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.automation import Automation
from mini_browser.gtk import GLib


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!doctype html><title>Agent fixture</title>
        <h1>Synthetic agent page</h1>
        <button id=go onclick="document.title='clicked'">Run fixture</button>
        <a id=next href=/next>Next fixture</a>
        <label>Name <input id=name oninput="document.title='filled:'+this.value"></label>
        <script>document.querySelector('input').onkeydown=e=>{
        if(e.key==='Enter') document.title='submitted';};</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-agent-smoke-") as temporary:
        root = Path(temporary)
        os.environ["MINI_BROWSER_AGENT_SOCKET"] = str(root / "agent" / "control.sock")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}/"
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            discover_devices=False,
            silent=True,
            application_id=f"io.github.stone.MiniBrowser.AgentSmoke.Run{os.getpid()}",
        )
        errors, completed = [], threading.Event()

        def on_main(callback):
            done, result = threading.Event(), {}

            def run_callback():
                try:
                    result["value"] = callback()
                except BaseException:
                    result["error"] = traceback.format_exc()
                finally:
                    done.set()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(run_callback)
            assert done.wait(5), "Main-thread activity check timed out"
            assert "error" not in result, result.get("error")
            return result.get("value")

        def check_activity():
            records = app.agent_activity.records
            assert 0 < len(records) <= 100
            assert any(record.method == "native_fill" for record in records)
            assert any(record.status == "Failed" for record in records)
            assert any(record.status == "Succeeded" for record in records)
            assert "synthetic-private-input" not in repr(records)
            for window in app.get_windows():
                window.home_sections.set_visible_child_name("agent")
                assert window.agent_actions_stack.get_visible_child_name() == "list"
                assert window.agent_actions_model.get_n_items() == len(records)
                assert window.agent_actions_model[0].record is records[0]

        def check_timeout():
            pending, replied = [], []
            cancelled = threading.Event()

            class PendingAutomation(Automation):
                def execute(self, _method, _params, finish):
                    pending.append(finish)

            on_main(
                lambda: PendingAutomation(app).dispatch(
                    "list_pages", {}, lambda *args, **kwargs: replied.append(args), cancelled
                )
            )
            record = on_main(lambda: app.agent_activity.records[0])
            assert on_main(lambda: bool(pending) and record.status == "Running")
            cancelled.set()
            deadline = time.monotonic() + 2
            while on_main(lambda: record.status) == "Running":
                assert time.monotonic() < deadline, "Timed-out activity stayed Running"
                time.sleep(0.05)
            assert record.status == "Timed out"
            on_main(lambda: pending[0]({"pages": []}))  # Late replies cannot change status.
            assert record.status == "Timed out" and not replied

        def cli(*arguments, success=True):
            run = subprocess.run(
                [sys.executable, "-m", "mini_browser", "agent", *map(str, arguments)],
                capture_output=True,
                text=True,
                timeout=22,
            )
            if success and run.returncode:
                raise AssertionError(f"CLI {arguments[0]}: {run.stderr}")
            if not success:
                assert run.returncode != 0
                return
            return json.loads(run.stdout)

        def wait_title(page, title):
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                pages = cli("list")["pages"]
                if any(
                    p["pageId"] == page and p["title"] == title and not p["loading"] for p in pages
                ):
                    return
                time.sleep(0.1)
            raise AssertionError(f"Title did not become {title}")

        def worker():
            try:
                deadline = time.monotonic() + 10
                while app.agent_control is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                initial = cli("list")["pages"][0]["pageId"]
                saved_url, saved_title = base + "saved", "Saved 中文 <app>"
                assert cli("apps")["apps"] == []
                cli("add-app", saved_url, "--title", saved_title)
                cli("add-app", saved_url)  # Repeating the URL keeps its explicit name.
                assert len(cli("apps")["apps"]) == 1
                assert len(cli("list")["pages"]) == 1  # Adding does not navigate/open.
                home = cli("inspect", initial)
                tile = next(
                    n["uid"]
                    for n in home["widgets"]
                    if n["type"] == "GtkButton"
                    and n.get("tooltip") == f"{saved_title}\n{saved_url}"
                )
                cli("native-click", initial, tile)
                wait_title(initial, "Agent fixture")
                assert cli("apps")["apps"][0]["title"] == saved_title
                cli("action", initial, "home")
                homes = cli("list")["pages"]
                assert len(homes) == 1 and homes[0]["layout"] == "home"
                assert homes[0]["pageId"] != initial
                initial = homes[0]["pageId"]
                cli("add-app", "javascript:alert(1)", success=False)
                page = cli("open", base)["pageId"]
                wait_title(page, "Agent fixture")
                snapshot = cli("snapshot", page)
                button = next(n["uid"] for n in snapshot["nodes"] if n["name"] == "Run fixture")
                cli("click", page, "--uid", button)
                wait_title(page, "clicked")
                text = "Synthetic 中文 '\" \\ value"
                cli("fill", page, "--selector", "#name", text)
                wait_title(page, "filled:" + text)
                cli("press", page, "Enter")
                wait_title(page, "submitted")
                value = cli("eval", page, "() => document.querySelector('#name').value")
                assert value["value"] == text
                cli("press", page, "Control+A")
                cli("press", page, "a")
                assert (
                    cli("eval", page, "() => document.querySelector('#name').value")["value"] == "a"
                )
                cdp = cli(
                    "cdp", page, "Runtime.evaluate", '{"expression":"21*2","returnByValue":true}'
                )
                assert cdp["result"]["value"] == 42
                cli("click", page, "--selector", "#next")
                wait_title(page, "Agent fixture")
                cli("screenshot", page, root / "web.png")
                cli("screenshot", page, root / "native.png", "--native")
                for file in ("web.png", "native.png"):
                    assert (root / file).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                cli("resize", page, 600, 700)
                cli("action", page, "settings")
                time.sleep(0.3)
                native = cli("inspect", page)
                assert any(n.get("type") == "AdwPreferencesDialog" for n in native["widgets"])
                cli("screenshot", page, root / "settings.png", "--native")
                # Use an observed GTK button, rather than directly touching the app.
                nodes = {n["uid"]: n for n in native["widgets"]}

                def in_dialog(node):
                    while node:
                        if node["type"] == "AdwPreferencesDialog":
                            return True
                        node = nodes.get(node.get("parent"))
                    return False

                close = next(
                    n["uid"]
                    for n in native["widgets"]
                    if n.get("type") == "GtkButton"
                    and in_dialog(n)
                    and ("close" in n.get("cssClasses", []) or "window-close" in n.get("icon", ""))
                )
                cli("native-click", page, close)
                time.sleep(0.2)
                cli("action", page, "home")
                homes = cli("list")["pages"]
                assert len(homes) == 1
                assert homes[0]["pageId"] == initial and homes[0]["layout"] == "home"
                page = initial
                # Keep another Home open while this reused window opens an app.
                initial = cli("open")["pageId"]
                native = cli("inspect", page)
                assert any(
                    n.get("tooltip") == f"{saved_title}\n{saved_url}" for n in native["widgets"]
                )
                entry = next(
                    n["uid"]
                    for n in native["widgets"]
                    if n["type"] == "GtkEntry"
                    and n.get("placeholder") == "Search or enter an address"
                )
                cli("native-fill", page, entry, "synthetic-private-input")
                native = cli("inspect", page)
                assert "synthetic-private-input" not in json.dumps(native)
                cli("native-fill", page, entry, base, success=False)  # Replaced snapshot.
                entry = next(n["uid"] for n in native["widgets"] if n["type"] == "GtkEntry")
                cli("native-fill", page, entry, base + "from-home", "--submit")
                wait_title(page, "Agent fixture")
                cli("action", page, "address")
                time.sleep(0.2)
                native = cli("inspect", page)
                entry = next(n["uid"] for n in native["widgets"] if n["type"] == "GtkEntry")
                cli("native-fill", page, entry, base + "from-address", "--submit")
                wait_title(page, "Agent fixture")
                cli("navigate", page, base + "next")
                wait_title(page, "Agent fixture")
                cli("click", page, "--uid", button, success=False)
                cli("click", 9999, "--selector", "button", success=False)
                # Real MCP process -> private transport -> real Chromium.
                messages = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25"},
                    },
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "take_snapshot", "arguments": {"pageId": page}},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "take_screenshot", "arguments": {"pageId": page}},
                    },
                ]
                mcp = subprocess.run(
                    [sys.executable, "-m", "mini_browser", "mcp"],
                    input="\n".join(map(json.dumps, messages)) + "\n",
                    text=True,
                    capture_output=True,
                    timeout=22,
                    check=True,
                )
                replies = [json.loads(line) for line in mcp.stdout.splitlines()]
                assert len(replies) == 3 and "isError" not in replies[1]["result"]
                assert replies[2]["result"]["content"][0]["type"] == "image"
                cli("action", initial, "home")
                cli("remove-app", saved_url)
                assert cli("apps")["apps"] == []
                home = cli("inspect", initial)
                assert all(
                    n.get("tooltip") != f"{saved_title}\n{saved_url}" for n in home["widgets"]
                )
                on_main(check_activity)
                check_timeout()
                if len(sys.argv) > 1:
                    time.sleep(0.2)
                    target = Path(sys.argv[1])
                    cli(
                        "screenshot",
                        initial,
                        target.with_name(target.stem + "-agent-actions.png"),
                        "--native",
                    )
                # Activity survives closing every window in an agent-enabled session.
                recorded = on_main(lambda: app.agent_activity.records[0])
                cli("close", page)
                cli("close", initial)
                assert cli("list")["pages"] == []
                cli("open")  # The --agent application hold survives its last window.
                assert on_main(lambda: recorded in app.agent_activity.records)
                on_main(check_activity)
                cli("stop")
                completed.set()
            except BaseException:
                errors.append(traceback.format_exc())
                GLib.idle_add(app.quit)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        watchdog = GLib.timeout_add_seconds(80, lambda: app.quit() or GLib.SOURCE_REMOVE)
        try:
            result = app.run(["agent-smoke", "--agent"])
            thread.join(timeout=2)
            if errors:
                raise AssertionError(errors[0])
            assert completed.is_set(), "Agent smoke did not finish"
            assert not Path(os.environ["MINI_BROWSER_AGENT_SOCKET"]).exists()
            print("Agent CLI/MCP GTK/Chromium smoke passed", flush=True)
            return result
        finally:
            GLib.source_remove(watchdog)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(run())
