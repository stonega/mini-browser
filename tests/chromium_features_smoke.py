"""CEF downloads, uploads, TLS rejection and persistent storage on synthetic sites."""

import datetime
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib


class Handler(BaseHTTPRequestHandler):
    visits = 0

    def do_GET(self):
        type(self).visits += 1
        payload = (
            b"synthetic download contents"
            if self.path == "/download"
            else (
                b"<!doctype html><title>Engine fixture</title>"
                b'<input type=file id=upload onchange="document.title=this.files[0].name">'
                b'<button style="position:absolute;left:20px;top:80px;width:240px;height:40px" '
                b"onclick=\"navigator.clipboard.writeText('synthetic page copy"
                b"\\n\\u526a\\u8cbc\\u7c3f')"
                b".then(()=>document.title='copy complete')"
                b".catch(e=>document.title='copy failed:'+e.name)\">Copy text</button>"
            )
        )
        self.send_response(200)
        if self.path == "/download":
            self.send_header("Content-Disposition", 'attachment; filename="fixture.txt"')
            self.send_header("Content-Type", "application/octet-stream")
        else:
            self.send_header("Content-Type", "text/html")
        if self.path == "/blocked":
            self.send_header("Permissions-Policy", "clipboard-write=()")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def tls_server(root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic loopback")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .sign(key, hashes.SHA256())
    )
    private, public = root / "synthetic.key", root / "synthetic.crt"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(public, private)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run(root, origin, secure=None, *, persistence_only=False):
    app = BrowserApplication(
        data_dir=root / "data",
        cache_dir=root / "cache",
        discover_devices=False,
        silent=True,
        application_id=f"io.github.stone.MiniBrowser.EngineSmoke.Run{os.getpid()}",
    )
    errors, state = [], {"step": "starting", "completed": False}

    def wait(label, predicate):
        state["step"] = label
        deadline = time.monotonic() + 12

        def ready():
            if time.monotonic() > deadline:
                raise TimeoutError(label)
            return predicate()

        yield ready

    def javascript(window, expression):
        result = []

        def completed(view, value):
            result.append(json.loads(view.evaluate_javascript_finish(value).to_json(0)))

        window.webview.evaluate_javascript(expression, -1, None, None, None, completed)
        yield from wait("script", lambda: bool(result))
        return result[0]

    def click_copy(window):
        valid, bounds = window.webview.compute_bounds(window)
        assert valid
        clicked = []

        def pointer_finished(connection, result):
            connection.call_finish(result)
            clicked.append(True)

        app.get_dbus_connection().call(
            "io.github.stone.MiniBrowser.TestPointer",
            "/io/github/stone/MiniBrowser/TestPointer",
            "io.github.stone.MiniBrowser.TestPointer",
            "Pointer",
            GLib.Variant(
                "(odddd)",
                (window.pin.path, bounds.get_x() + 40, bounds.get_y() + 100, 0.0, 0.0),
            ),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            pointer_finished,
        )
        yield from wait("real copy click", lambda: bool(clicked))
        yield from wait("page copy", lambda: window.webview.get_title().startswith("copy "))

    def clipboard_text(window):
        copied = []

        def read_clipboard(clipboard, result):
            try:
                copied.append(clipboard.read_text_finish(result))
            except GLib.Error:
                copied.append(None)

        window.webview.get_clipboard().read_text_async(None, read_clipboard)
        yield from wait("native clipboard", lambda: bool(copied))
        return copied[0]

    def scenario(window):
        window.load_uri(origin)
        yield from wait("page", lambda: not window.webview.is_loading())
        if persistence_only:
            stored = yield from javascript(
                window, "({cookie:document.cookie,storage:localStorage.getItem('fixture')})"
            )
            assert stored == {"cookie": "fixture=persisted", "storage": "saved"}, stored
        else:
            yield from javascript(
                window,
                "document.cookie='fixture=persisted; Path=/; Max-Age=3600'; "
                "localStorage.setItem('fixture','saved'); true",
            )
            # Both native refresh actions must request the page again.
            for action in ("reload", "reload-bypass-cache"):
                visits = Handler.visits
                window.lookup_action(action).activate(None)
                yield from wait(
                    action,
                    lambda visits=visits: (
                        not window.webview.is_loading() and Handler.visits > visits
                    ),
                )
            capabilities = yield from javascript(
                window, "({secure:isSecureContext,clipboard:!!navigator.clipboard})"
            )
            assert capabilities == {"secure": True, "clipboard": True}, capabilities
            yield from click_copy(window)
            assert window.webview.get_title() == "copy complete", window.webview.get_title()
            expected = "synthetic page copy\n剪貼簿"
            assert (yield from clipboard_text(window)) == expected
            window.webview.page_clipboard.reset()
            denied = yield from javascript(
                window, "navigator.clipboard.writeText('background').then(()=>true,e=>e.name)"
            )
            assert denied == "NotAllowedError", denied
            denied = yield from javascript(
                window, "navigator.clipboard.readText().then(()=>true,e=>e.name)"
            )
            assert denied == "NotAllowedError", denied
            assert (yield from clipboard_text(window)) == expected
            window.load_uri(origin + "blocked")
            yield from wait("clipboard policy page", lambda: not window.webview.is_loading())
            yield from click_copy(window)
            assert window.webview.get_title() == "copy failed:NotAllowedError"
            assert (yield from clipboard_text(window)) == expected
            destination, upload = root / "download.txt", root / "upload.txt"
            upload.write_text("synthetic upload")

            class Picker:
                def __init__(self, **_options):
                    pass

                def save(self, _parent, _cancel, callback):
                    GLib.idle_add(callback, self, None)

                def save_finish(self, _result):
                    return Gio.File.new_for_path(str(destination))

                def open(self, _parent, _cancel, callback):
                    GLib.idle_add(callback, self, None)

                def open_finish(self, _result):
                    return Gio.File.new_for_path(str(upload))

            with patch("mini_browser.chromium.Gtk.FileDialog", Picker):
                yield from javascript(window, "document.querySelector('#upload').click();true")
                yield from wait("upload chosen", lambda: window.webview.get_title() == "upload.txt")
                yield from javascript(window, "location.href='/download';true")
                yield from wait(
                    "download saved",
                    lambda: destination.is_file() and not app.network_session.downloads,
                )
                assert destination.read_bytes() == b"synthetic download contents"
            window.load_uri(secure)
            yield from wait("TLS certificate rejected", lambda: window.failed)
            assert "security certificate" in window.error_page.get_description()
            assert window.stack.get_visible_child_name() == "error"
        state["completed"] = True
        window.close()

    def tick():
        try:
            if "steps" not in state:
                if not app.get_windows():
                    return GLib.SOURCE_CONTINUE
                state["steps"] = scenario(app.get_windows()[0])
                state["waiting"] = next(state["steps"])
            elif state["waiting"]():
                state["waiting"] = next(state["steps"])
        except StopIteration:
            return GLib.SOURCE_REMOVE
        except Exception as error:
            errors.append(str(error))
            traceback.print_exc()
            for window in app.get_windows():
                window.close()
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(50, tick)
    result = app.run([sys.argv[0]])
    if errors or not state["completed"]:
        raise AssertionError(errors or state["step"])
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check-profile":
        return run(Path(sys.argv[2]), sys.argv[3], persistence_only=True)
    with tempfile.TemporaryDirectory(prefix="mini-browser-engine-fixture-") as directory:
        root = Path(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        secure = tls_server(root)
        origin = f"http://127.0.0.1:{server.server_port}/"
        try:
            result = run(root, origin, f"https://127.0.0.1:{secure.server_port}/")
            # New process, same generated profile: CEF cannot be reinitialized
            # safely in a process that has already called CefShutdown.
            subprocess.run(
                [sys.executable, __file__, "--check-profile", str(root), origin],
                check=True,
                timeout=30,
            )
            print(
                "PASS: page Copy to GTK clipboard, background/read/policy guards, "
                "both refresh actions, native upload/download, TLS rejection, "
                "cookies and local storage across restart",
                flush=True,
            )
            return result
        finally:
            server.shutdown()
            secure.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
