import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from mini_browser.page_clipboard import BINDING, LIMIT, WORLD, PageClipboard, secure_origin


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "id": 7,
            "uniqueId": "document-a",
            "name": WORLD,
            "origin": "http://127.0.0.1:3080",
            "auxData": {"frameId": "main"},
        }
        self.clipboard = Mock()
        self.view = SimpleNamespace(
            contexts={7: self.context},
            generation=1,
            closed=False,
            get_uri=Mock(return_value="http://127.0.0.1:3080/session"),
            get_root=Mock(return_value=SimpleNamespace(is_active=Mock(return_value=True))),
            get_mapped=Mock(return_value=True),
            has_focus=Mock(return_value=True),
            get_clipboard=Mock(return_value=self.clipboard),
            command=Mock(),
        )
        self.bridge = PageClipboard(self.view)
        self.bridge.user_input()

    def request(self, text="Synthetic\n剪貼簿", **overrides):
        event = {
            "name": BINDING,
            "executionContextId": 7,
            "payload": json.dumps({"id": 1, "text": text}, ensure_ascii=False),
            **overrides,
        }
        self.bridge.handle(event)

    def confirm(self, *, frame="main", error=None):
        self.view.command.call_args.kwargs["callback"](
            {"frameTree": {"frame": {"id": frame}}}, error
        )

    def assert_denied(self):
        self.clipboard.set.assert_not_called()
        reply = self.view.command.call_args.args[1]
        self.assertTrue(reply["arguments"][1]["value"])

    def test_write_after_user_action_and_typed_reply(self):
        self.request()
        self.confirm()
        self.clipboard.set.assert_called_once_with("Synthetic\n剪貼簿")
        reply = self.view.command.call_args.args[1]
        self.assertEqual(reply["uniqueContextId"], "document-a")
        self.assertEqual(reply["arguments"], [{"value": 1}, {"value": False}])
        self.assertNotIn("Synthetic", reply["functionDeclaration"])

    def test_hidden_unfocused_inactive_and_expired_requests_are_denied(self):
        for change in ("hidden", "unfocused", "inactive", "expired", "no action", "reset"):
            with self.subTest(change=change):
                self.setUp()
                if change == "hidden":
                    self.view.get_mapped.return_value = False
                elif change == "unfocused":
                    self.view.has_focus.return_value = False
                elif change == "inactive":
                    self.view.get_root.return_value.is_active.return_value = False
                elif change == "expired":
                    self.bridge.last_input = time.monotonic() - 6
                elif change == "reset":
                    self.bridge.reset()
                else:
                    self.bridge.last_input = 0
                self.request()
                self.confirm()
                self.assert_denied()

    def test_origin_frame_and_insecure_http_guards(self):
        for uri, frame in (
            ("http://remote.example/session", "main"),
            ("https://other.example/session", "main"),
            ("http://127.0.0.1:3081/session", "main"),
            ("http://127.0.0.1:3080/session", "child"),
        ):
            with self.subTest(uri=uri, frame=frame):
                self.setUp()
                self.view.get_uri.return_value = uri
                self.request()
                self.confirm(frame=frame)
                self.assert_denied()

    def test_navigation_close_and_context_replacement_drop_stale_results(self):
        for change in ("navigate", "close", "context"):
            with self.subTest(change=change):
                self.setUp()
                self.request()
                if change == "navigate":
                    self.view.generation += 1
                elif change == "close":
                    self.view.closed = True
                else:
                    self.view.contexts[7] = {**self.context, "uniqueId": "document-b"}
                self.confirm()
                self.clipboard.set.assert_not_called()
                self.assertEqual(self.view.command.call_count, 1)

    def test_untrusted_world_and_malformed_payloads_are_ignored(self):
        for payload in ("null", "[]", "{", '{"id":true}', '{"id":-1}', "x" * (LIMIT * 2 + 1)):
            with self.subTest(payload=payload[:30]):
                self.request(payload=payload)
                self.view.command.assert_not_called()
        self.context["name"] = ""
        self.request()
        self.view.command.assert_not_called()

    def test_invalid_text_and_oversized_utf8_are_denied(self):
        for text in (None, ["text"], "embedded\0nul", "\ud800", "界" * (LIMIT // 3 + 1)):
            with self.subTest(kind=type(text).__name__):
                self.setUp()
                self.request(text)
                self.confirm()
                self.assert_denied()

    def test_secure_origin_normalization(self):
        self.assertEqual(
            secure_origin("https://example.org:443/path"), ("https", "example.org", 443)
        )
        for uri in ("http://localhost:3080", "http://127.0.0.1:3080", "http://[::1]:3080"):
            self.assertIsNotNone(secure_origin(uri))
        for uri in (
            "http://example.org",
            "http://localhost.evil",
            "file:///tmp/a",
            "https://u:p@a",
        ):
            self.assertIsNone(secure_origin(uri))
