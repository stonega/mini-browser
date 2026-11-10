import base64
import json
import struct
import time
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import Mock

from mini_browser.gtk import Gdk
from mini_browser.page_clipboard import (
    BINDING,
    IMAGE_LIMIT,
    LIMIT,
    PAYLOAD_LIMIT,
    PIXEL_LIMIT,
    WORLD,
    PageClipboard,
    clipboard_content,
    clipboard_origin,
)


def png_fixture(width=2, height=1):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes([17, 90, 160, 255]) * width) * height))
        + chunk(b"IEND", b"")
    )


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
        self.clipboard.set_content.assert_not_called()
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

    def test_image_and_mixed_copies_advertise_the_serializable_texture_type(self):
        for text in (None, "Synthetic alternative"):
            with self.subTest(text=text):
                self.setUp()
                request = {"id": 1, "png": base64.b64encode(png_fixture()).decode("ascii")}
                if text is not None:
                    request["text"] = text
                self.request(payload=json.dumps(request))
                self.confirm()
                self.clipboard.set.assert_not_called()
                provider = self.clipboard.set_content.call_args.args[0]
                self.assertTrue(provider.ref_formats().contain_gtype(Gdk.Texture))
                self.assertNotIn("GdkMemoryTexture", provider.ref_formats().to_string())
                self.assertEqual(
                    self.view.command.call_args.args[1]["arguments"][1], {"value": False}
                )

    def test_invalid_images_are_denied_without_replacing_existing_clipboard_content(self):
        png = png_fixture()
        cases = [
            None,
            42,
            "not base64!",
            base64.b64encode(b"not an image").decode(),
            "A" * ((IMAGE_LIMIT + 2) // 3 * 4 + 1),
            base64.b64encode(png[:16] + struct.pack(">II", 0, 1) + png[24:]).decode(),
            base64.b64encode(png[:16] + struct.pack(">II", PIXEL_LIMIT + 1, 1) + png[24:]).decode(),
            base64.b64encode(png[:24]).decode(),
        ]
        for image in cases:
            with self.subTest(image_type=type(image).__name__):
                self.setUp()
                self.request(payload=json.dumps({"id": 1, "text": "valid text", "png": image}))
                self.confirm()
                self.assert_denied()
        self.setUp()
        self.clipboard.set_content.return_value = False
        self.request(payload=json.dumps({"id": 1, "png": base64.b64encode(png).decode()}))
        self.confirm()
        self.assertTrue(self.view.command.call_args.args[1]["arguments"][1]["value"])

    def test_content_bounds_and_escaped_unicode(self):
        self.assertEqual(
            clipboard_content({"text": "", "png": base64.b64encode(png_fixture()).decode()}),
            ("", png_fixture()),
        )
        with self.assertRaises(ValueError):
            clipboard_content({})
        text = "😀" * (LIMIT // 4)
        self.request(payload=json.dumps({"id": 1, "text": text}))
        self.confirm()
        self.clipboard.set.assert_called_once_with(text)

    def test_legacy_http_copies_remain_origin_bound_and_cannot_carry_images(self):
        for kind, permitted in (("legacy", True), ("write", False), ("unknown", False)):
            with self.subTest(kind=kind):
                self.setUp()
                self.view.get_uri.return_value = "http://example.test/page"
                self.context["origin"] = "http://example.test"
                self.request(payload=json.dumps({"id": 1, "kind": kind, "text": "legacy copy"}))
                self.confirm()
                if permitted:
                    self.clipboard.set.assert_called_once_with("legacy copy")
                else:
                    self.assert_denied()
        self.setUp()
        self.request(
            payload=json.dumps(
                {"id": 1, "kind": "legacy", "png": base64.b64encode(png_fixture()).decode()}
            )
        )
        self.confirm()
        self.assert_denied()

    def test_delayed_paste_is_dropped_after_navigation_focus_change_or_new_paste(self):
        for change in ("navigate", "close", "focus", "reset", "new paste"):
            with self.subTest(change=change):
                self.setUp()
                self.bridge.paste(plain=True)
                ready = self.clipboard.read_text_async.call_args.args[1]
                if change == "navigate":
                    self.view.generation += 1
                elif change == "close":
                    self.view.closed = True
                elif change == "focus":
                    self.view.has_focus.return_value = False
                elif change == "reset":
                    self.bridge.reset()
                else:
                    self.bridge.paste(plain=True)
                self.clipboard.read_text_finish.return_value = "late desktop data"
                ready(self.clipboard, None)
                self.view.command.assert_not_called()
                self.clipboard.read_texture_async.assert_not_called()

    def test_image_paste_requires_document_checks_and_successful_preparation(self):
        for change in ("success", "navigate", "context", "unfocus", "error", "denied"):
            with self.subTest(change=change):
                self.setUp()
                encoded = base64.b64encode(png_fixture()).decode()
                self.bridge._paste_image(encoded, lambda: self.bridge._active(1))
                self.confirm()
                parameters = self.view.command.call_args.args[1]
                self.assertEqual(parameters["uniqueContextId"], "document-a")
                self.assertEqual(parameters["arguments"], [{"value": encoded}])
                self.assertNotIn(encoded, parameters["functionDeclaration"])
                self.assertTrue(parameters["userGesture"])
                prepared = self.view.command.call_args.args[2]
                if change == "navigate":
                    self.view.generation += 1
                elif change == "context":
                    self.view.contexts[7] = {**self.context, "uniqueId": "document-b"}
                elif change == "unfocus":
                    self.view.has_focus.return_value = False
                prepared(
                    {"result": {"value": change != "denied"}},
                    "error" if change == "error" else None,
                )
                self.assertEqual(self.view.command.call_count, 4 if change == "success" else 2)
                if change == "success":
                    self.assertEqual(
                        self.view.command.call_args_list[-2].args[1]["commands"], ["Paste"]
                    )

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
        for payload in ("null", "[]", "{", '{"id":true}', '{"id":-1}', "x" * (PAYLOAD_LIMIT + 1)):
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

    def test_clipboard_origin_normalization(self):
        self.assertEqual(
            clipboard_origin("https://example.org:443/path"), ("https", "example.org", 443)
        )
        for uri in ("http://localhost:3080", "http://127.0.0.1:3080", "http://[::1]:3080"):
            self.assertIsNotNone(clipboard_origin(uri))
        for uri in (
            "http://example.org",
            "http://localhost.evil",
            "file:///tmp/a",
            "https://u:p@a",
        ):
            self.assertIsNone(clipboard_origin(uri))

    def test_only_the_views_trusted_extension_origin_can_copy(self):
        origin = "chrome-extension://" + "a" * 32
        self.assertIsNone(clipboard_origin(origin))
        self.assertEqual(
            clipboard_origin(origin + "/popup.html", extension_origin=origin),
            ("chrome-extension", "a" * 32, None),
        )
        for uri in (
            "chrome-extension://" + "b" * 32,
            origin + ":80",
            origin + ".",
            "chrome-extension://user@" + "a" * 32,
        ):
            self.assertIsNone(clipboard_origin(uri, extension_origin=origin))
        for context_origin in (origin, "chrome-extension://" + "b" * 32):
            with self.subTest(origin=context_origin):
                self.setUp()
                self.view.extension_origin = origin
                self.view.get_uri.return_value = origin + "/popup.html"
                self.context["origin"] = context_origin
                self.request()
                self.confirm()
                if context_origin == origin:
                    self.clipboard.set.assert_called_once_with("Synthetic\n剪貼簿")
                else:
                    self.assert_denied()
