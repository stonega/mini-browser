"""Boundaries of local agent transport and the actual stdio MCP entry point."""

import base64
import io
import json
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_browser.agent_cli import main, save_png
from mini_browser.agent_tools import validate
from mini_browser.control import LIMIT, ControlError, ControlServer, receive, request
from mini_browser.mcp import rpc


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "private" / "agent.sock"

    def server(self, dispatch=None):
        server = ControlServer(
            dispatch or (lambda m, p, done, _: done({"method": m, "params": p})), path=self.path
        )
        self.addCleanup(server.close)
        return server

    def test_private_transport_round_trip_unicode_and_cleanup(self):
        server = self.server()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        result = request("fill", {"value": "synthetic 中文\n'\""}, path=self.path)
        self.assertEqual(result["params"]["value"], "synthetic 中文\n'\"")
        server.close()
        self.assertFalse(self.path.exists())

    def test_refuse_live_socket_and_preserve_other_paths(self):
        self.server()
        with self.assertRaises(ControlError):
            ControlServer(lambda *_: None, path=self.path)
        self.assertTrue(self.path.is_socket())
        other = self.path.parent / "regular"
        other.write_text("preserve")
        with self.assertRaises(ControlError):
            ControlServer(lambda *_: None, path=other)
        self.assertEqual(other.read_text(), "preserve")
        link = self.path.parent / "link"
        link.symlink_to(other)
        with self.assertRaises(ControlError):
            ControlServer(lambda *_: None, path=link)
        self.assertTrue(link.is_symlink())

    def test_remove_stale_socket_and_refuse_insecure_directory(self):
        self.path.parent.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.bind(str(self.path))
        self.server()
        other = self.path.parent / "public"
        other.mkdir(mode=0o755)
        with self.assertRaises(ControlError):
            ControlServer(lambda *_: None, path=other / "agent.sock")

    def test_errors_and_malformed_messages(self):
        self.server(lambda _m, _p, done, _: done(error="Page disappeared. Take a fresh snapshot."))
        with self.assertRaisesRegex(ControlError, "fresh snapshot"):
            request("click", path=self.path)
        for value in (b"[]\n", b"{\n", b"", b"{}", b"x" * (LIMIT + 1)):
            with self.subTest(size=len(value)), self.assertRaises(ControlError):
                receive(io.BytesIO(value))
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.path))
            connection.sendall(b'{"method":null}\n')
            with connection.makefile("rb") as stream:
                self.assertIn("error", receive(stream))
        # An invalid request does not stop subsequent clients.
        with self.assertRaisesRegex(ControlError, "fresh snapshot"):
            request("click", path=self.path)

    def test_mcp_actual_process_negotiates_and_calls_browser(self):
        self.server()
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_pages", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "navigate_page", "arguments": {"pageId": True}},
            },
        ]
        run = subprocess.run(
            [sys.executable, "-m", "mini_browser", "mcp", "--socket", str(self.path)],
            input="\n".join(map(json.dumps, messages)) + "\n",
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(run.stderr, "")
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("cdp_send", [t["name"] for t in replies[1]["result"]["tools"]])
        value = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertEqual(value["method"], "list_pages")
        self.assertEqual(replies[3]["error"]["code"], -32602)

    def test_mcp_errors_images_and_notifications(self):
        self.assertEqual(rpc([])["error"]["code"], -32600)
        self.assertEqual(
            rpc({"jsonrpc": "2.0", "id": 0, "method": "missing"})["error"]["code"], -32601
        )
        self.assertIsNone(rpc({"jsonrpc": "2.0", "method": "notifications/cancelled"}))
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "take_screenshot", "arguments": {"pageId": 1}},
        }
        with patch("mini_browser.mcp.request", return_value={"data": "png-fixture"}):
            reply = rpc(message)
        self.assertEqual(reply["result"]["content"][0]["type"], "image")

    def test_cli_does_not_start_gtk_and_reports_json(self):
        self.server()
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(
                main(["--socket", str(self.path), "fill", "1", "--selector", "input", "中文"]), 0
            )
        result = json.loads(out.getvalue())
        self.assertEqual(result["params"]["value"], "中文")
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["call", "fill", "[]"]), 1)
        self.assertIn("error", json.loads(err.getvalue()))

    def test_schema_and_screenshot_preserve_files(self):
        for name, arguments in (
            ("fill", {"pageId": 1}),
            ("list_pages", {"secret": "x"}),
            ("close_page", {"pageId": True}),
            ("resize_page", {"pageId": 1, "width": 0, "height": 200}),
        ):
            with self.assertRaises(ControlError):
                validate(name, arguments)
        data = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
        target = Path(self.temporary.name) / "shot.png"
        save_png(target, data)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            save_png(target, data)
        link = target.with_name("link.png")
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            save_png(link, data)
        self.assertEqual(target.read_bytes(), base64.b64decode(data))

    def test_home_app_cli_and_mcp_tools_have_no_page_requirement(self):
        self.server()
        for arguments, method in (
            (["add-app", "https://example.org", "--title", "Docs 中文"], "add_home_app"),
            (["apps"], "list_home_apps"),
            (["remove-app", "https://example.org"], "remove_home_app"),
        ):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main(["--socket", str(self.path), *arguments]), 0)
            self.assertEqual(json.loads(out.getvalue())["method"], method)
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "add_home_app", "arguments": {"url": "https://example.org"}},
        }
        result = rpc(message, path=self.path)
        self.assertEqual(
            json.loads(result["result"]["content"][0]["text"])["method"], "add_home_app"
        )


if __name__ == "__main__":
    unittest.main()
