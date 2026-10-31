"""Real CEF hit-testing and native page menus on synthetic loopback content."""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib, Gtk
from mini_browser.page_context import COPY, CUT, PASTE

PAGE = b"""<!doctype html><title>Context fixture</title><style>
body {margin:0} #selection {position:absolute;left:20px;top:40px}
input {position:absolute;left:20px;top:100px;width:220px}
#password {top:150px} a {position:absolute;left:20px;top:200px}
img {position:absolute;left:20px;top:260px;width:100px;height:60px}
iframe {position:absolute;left:20px;top:350px;width:240px;height:100px}
</style><p id=selection>Synthetic selected text</p><input id=edit value='Synthetic editable'>
<input id=password type=password value='synthetic-secret'>
<a href='/target'>Synthetic link</a><img src='/image.svg'>
<iframe src='/frame'></iframe>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload, kind = PAGE, "text/html"
        if self.path == "/image.svg":
            payload = (
                b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
                b'<rect width="100" height="60" fill="teal"/></svg>'
            )
            kind = "image/svg+xml"
        elif self.path == "/frame":
            payload = b"<!doctype html><p>Frame selection</p>"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def run(root, origin):
    app = BrowserApplication(
        data_dir=root / "data",
        cache_dir=root / "cache",
        discover_devices=False,
        silent=True,
        application_id=f"io.github.stone.MiniBrowser.EngineSmoke.Context{os.getpid()}",
    )
    state, errors = {"completed": False}, []

    def wait(label, predicate):
        state["step"] = label
        deadline = time.monotonic() + 10
        while not predicate():
            assert time.monotonic() < deadline, label
            yield

    def script(view, expression):
        values = []

        def completed(view, result):
            values.append(json.loads(view.evaluate_javascript_finish(result).to_json(0)))

        view.evaluate_javascript(expression, -1, None, None, None, completed)
        yield from wait("script", lambda: bool(values))
        return values[0]

    def menu(view, x, y):
        yield from wait("previous menu closed", lambda: view.context_menu is None)
        view.grab_focus()
        window = view.get_root()
        valid, bounds = view.compute_bounds(window)
        assert valid
        clicked = []

        def finished(connection, result):
            connection.call_finish(result)
            clicked.append(True)

        app.get_dbus_connection().call(
            "io.github.stone.MiniBrowser.TestPointer",
            "/io/github/stone/MiniBrowser/TestPointer",
            "io.github.stone.MiniBrowser.TestPointer",
            "Click",
            GLib.Variant("(oddu)", (window.pin.path, bounds.get_x() + x, bounds.get_y() + y, 3)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            finished,
        )
        yield from wait("right click", lambda: bool(clicked))
        yield from wait("context menu", lambda: view.context_menu is not None)
        return view.context_menu

    def clipboard(view):
        values = []

        def completed(clipboard, result):
            try:
                values.append(clipboard.read_text_finish(result))
            except GLib.Error:
                values.append(None)

        view.get_clipboard().read_text_async(None, completed)
        yield from wait("clipboard", lambda: bool(values))
        return values[0]

    def choose(menu, action):
        assert menu.buttons[action].get_sensitive(), action
        menu.buttons[action].emit("clicked")

    def scenario(window):
        view = window.webview
        window.load_uri(origin)
        yield from wait("fixture ready", lambda: not view.is_loading() and view.get_title())
        page = yield from menu(view, 320, 25)
        assert COPY not in page.buttons and CUT not in page.buttons and PASTE not in page.buttons
        assert not page.buttons[100].get_sensitive()  # Back
        assert {102, 103, "save-page", "save-pdf", "source", "agent"} <= page.buttons.keys()
        if len(sys.argv) > 1:
            yield from wait("menu mapped", lambda: page.popover.get_mapped())
            yield from wait("menu allocated", lambda: page.popover.get_width() > 0)
            paintable = Gtk.WidgetPaintable.new(page.popover)
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, page.popover.get_width(), page.popover.get_height())
            texture = window.get_renderer().render_texture(snapshot.to_node(), None)
            texture.save_to_png(sys.argv[1])
        choose(page, "agent")
        prompt = yield from clipboard(view)
        assert "agent snapshot PAGE" in prompt and origin in prompt
        yield from script(
            view,
            "getSelection().selectAllChildren(document.querySelector('#selection'));true",
        )
        selected = yield from menu(view, 80, 60)
        assert selected.context["selection"] == "Synthetic selected text", selected.context
        choose(selected, "agent")
        prompt = yield from clipboard(view)
        assert '"selected_text": "Synthetic selected text"' in prompt
        selected = yield from menu(view, 80, 60)
        choose(selected, COPY)
        assert (yield from clipboard(view)) == "Synthetic selected text"
        yield from script(view, "getSelection().removeAllRanges();true")
        link = yield from menu(view, 70, 210)
        assert link.context["link_url"] == origin + "target", link.context
        choose(link, "copy-link")
        assert (yield from clipboard(view)) == origin + "target"
        link = yield from menu(view, 70, 210)
        choose(link, "open-link")
        yield from wait("explicit new window", lambda: len(app.get_windows()) == 2)
        other = next(candidate for candidate in app.get_windows() if candidate is not window)
        yield from wait("new window loaded", lambda: not other.webview.is_loading())
        assert view.get_uri() == origin
        assert other.webview.get_uri() == origin + "target"
        other.close()
        window.present()
        image = yield from menu(view, 60, 280)
        assert image.context["media_type"] == 1, image.context
        choose(image, "agent")
        assert f'"image_url": "{origin}image.svg"' in (yield from clipboard(view))
        yield from script(view, "document.querySelector('#edit').focus();edit.select();true")
        editable = yield from menu(view, 70, 110)
        assert editable.context["editable"]
        assert editable.buttons[PASTE].get_sensitive(), editable.context
        choose(editable, CUT)
        yield from wait("cut", lambda: view.context_menu is None)
        assert (yield from clipboard(view)) == "Synthetic editable"
        assert (yield from script(view, "edit.value")) == ""
        view.get_clipboard().set("Pasted synthetic text")
        editable = yield from menu(view, 70, 110)
        assert (yield from clipboard(view)) == "Pasted synthetic text"
        choose(editable, PASTE)
        yield from wait("paste menu closed", lambda: view.context_menu is None)
        deadline = time.monotonic() + 10
        while (yield from script(view, "edit.value")) != "Pasted synthetic text":
            assert time.monotonic() < deadline, "paste completed"
        yield from script(view, "password.focus();password.select();true")
        password_menu = yield from menu(view, 70, 160)
        assert not password_menu.context.get("selection"), password_menu.context
        assert not password_menu.buttons[COPY].get_sensitive()
        choose(password_menu, "agent")
        assert "synthetic-secret" not in (yield from clipboard(view))
        yield from script(
            view,
            "const f=document.querySelector('iframe');"
            "f.contentWindow.getSelection().selectAllChildren(f.contentDocument.querySelector('p'));"
            "true",
        )
        frame = yield from menu(view, 70, 385)
        assert frame.context["selection"] == "Frame selection", frame.context
        choose(frame, COPY)
        assert (yield from clipboard(view)) == "Frame selection"
        destination = root / "page.mhtml"

        class Picker:
            def __init__(self, **_options):
                pass

            def save(self, _parent, _cancel, callback):
                GLib.idle_add(callback, self, None)

            def save_finish(self, _result):
                return Gio.File.new_for_path(str(destination))

        with patch("mini_browser.page_menu.Gtk.FileDialog", Picker):
            page = yield from menu(view, 320, 25)
            choose(page, "save-page")
            yield from wait(
                "page saved", lambda: destination.is_file() and destination.stat().st_size
            )
            assert b"multipart/related" in destination.read_bytes()
            destination = root / "page.pdf"
            page = yield from menu(view, 320, 25)
            choose(page, "save-pdf")
            yield from wait(
                "pdf saved", lambda: destination.is_file() and destination.stat().st_size
            )
            assert destination.read_bytes().startswith(b"%PDF-")
            destination = root / "image.svg"
            image = yield from menu(view, 60, 280)
            choose(image, "save-media")
            yield from wait(
                "image saved", lambda: destination.is_file() and not app.network_session.downloads
            )
            assert b"<svg" in destination.read_bytes()
        printed = []

        class Printer:
            def __init__(self, **_options):
                pass

            def print_file(self, _parent, _setup, file, _cancel, callback):
                assert Path(file.get_path()).read_bytes().startswith(b"%PDF-")
                printed.append(file.get_path())
                GLib.idle_add(callback, self, None)

            def print_file_finish(self, _result):
                return True

        with patch("mini_browser.page_menu.Gtk.PrintDialog", Printer):
            page = yield from menu(view, 320, 25)
            choose(page, "print")
            yield from wait("print handoff", lambda: bool(printed))
            yield from wait("print cleanup", lambda: not Path(printed[0]).exists())
        page = yield from menu(view, 320, 25)
        choose(page, "source")
        yield from wait("source dialog", lambda: bool(view.dialogs))
        for dialog in tuple(view.dialogs):
            dialog.force_close()
        page = yield from menu(view, 320, 25)
        window.load_uri(origin + "target")
        yield from wait("navigation dismisses menu", lambda: view.context_menu is None)
        assert page.answered
        yield from wait("target loaded", lambda: not view.is_loading())
        page = yield from menu(view, 320, 25)
        assert page.buttons[100].get_sensitive()
        choose(page, 100)
        yield from wait("back", lambda: view.get_uri() == origin and not view.is_loading())
        page = yield from menu(view, 320, 25)
        page.close()  # cancellation must release CEF's custom menu callback
        page = yield from menu(view, 320, 25)
        window.close()  # closing with a menu must not retain a native callback
        assert page.answered
        state["completed"] = True

    def tick():
        try:
            if "steps" not in state:
                if not app.get_windows():
                    return GLib.SOURCE_CONTINUE
                state["steps"] = scenario(app.get_windows()[0])
            next(state["steps"])
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
    app.run([sys.argv[0]])
    assert not errors and state["completed"], errors or state.get("step")


def main():
    with tempfile.TemporaryDirectory(prefix="mini-browser-context-") as temporary:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            run(Path(temporary), f"http://127.0.0.1:{server.server_port}/")
            print(
                "PASS: page/link/image/selection/edit/frame menus, agent prompts, "
                "save/PDF/source, lifecycle"
            )
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
