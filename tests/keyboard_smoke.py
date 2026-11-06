"""Real Wayland keyboard input against a synthetic Chromium page."""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gdk, Gio, GLib


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!doctype html><title>Keyboard fixture</title>
        <style>textarea{position:absolute;left:20px;top:20px;width:300px;height:150px}</style>
        <button id=previous>Previous</button>
        <textarea id=edit>synthetic text</textarea><button id=next>Next</button>
        <script>
        window.keys=[];
        for(const type of ['keydown','keyup'])document.addEventListener(type,e=>{
            keys.push({type:e.type,key:e.key,code:e.code,keyCode:e.keyCode,
                ctrl:e.ctrlKey,shift:e.shiftKey,alt:e.altKey,meta:e.metaKey});
        });
        </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def run(root, origin):
    app = BrowserApplication(
        data_dir=root / "data",
        cache_dir=root / "cache",
        discover_devices=False,
        silent=True,
        application_id=f"io.github.stone.MiniBrowser.KeyboardSmoke.Run{os.getpid()}",
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

    def interact(window, method, signature, arguments):
        values = []

        def completed(connection, result):
            try:
                connection.call_finish(result)
                values.append(None)
            except GLib.Error as error:
                values.append(error)

        app.get_dbus_connection().call(
            "io.github.stone.MiniBrowser.TestPointer",
            "/io/github/stone/MiniBrowser/TestPointer",
            "io.github.stone.MiniBrowser.TestPointer",
            method,
            GLib.Variant(signature, (window.pin.path, *arguments)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            completed,
        )
        yield from wait(method, lambda: bool(values))
        assert values == [None], values

    def keys(window, *values):
        yield from interact(window, "Keys", "(oau)", (values,))

    def scenario(window):
        view = window.webview
        window.load_uri(origin)
        yield from wait("fixture", lambda: not view.is_loading() and view.get_title())
        valid, bounds = view.compute_bounds(window)
        assert valid
        yield from interact(
            window, "Click", "(oddu)", (bounds.get_x() + 40, bounds.get_y() + 40, 1)
        )
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_a)
        result = yield from script(view, "({start:edit.selectionStart,end:edit.selectionEnd,keys})")
        assert result["start"] == 0 and result["end"] == len("synthetic text"), result
        assert result["keys"][0]["key"] == "Control" and result["keys"][0]["ctrl"], result
        assert not result["keys"][-1]["ctrl"], result
        for chord, expected_key, expected_code in (
            ((Gdk.KEY_Control_L, Gdk.KEY_i), "i", 73),
            ((Gdk.KEY_Control_L, Gdk.KEY_k), "k", 75),
            ((Gdk.KEY_Control_L, Gdk.KEY_p), "p", 80),
            ((Gdk.KEY_Control_L, Gdk.KEY_Shift_L, Gdk.KEY_c), "C", 67),
            ((Gdk.KEY_Control_L, Gdk.KEY_semicolon), ";", 186),
            ((Gdk.KEY_Shift_L, Gdk.KEY_Tab), "Tab", 9),
            ((Gdk.KEY_F2,), "F2", 113),
        ):
            yield from script(view, "keys.length=0;true")
            yield from keys(window, *chord)
            result = yield from script(view, "keys")
            down = [event for event in result if event["type"] == "keydown"][-1]
            assert down["key"] == expected_key and down["keyCode"] == expected_code, result
            assert down["ctrl"] == (Gdk.KEY_Control_L in chord), result
            assert down["shift"] == (Gdk.KEY_Shift_L in chord), result
            assert all(event["key"] != "\0" for event in result), result
        assert (yield from script(view, "document.activeElement.id")) == "previous"
        yield from keys(window, Gdk.KEY_Tab)
        assert (yield from script(view, "document.activeElement.id")) == "edit"

        # Native editing commands and GTK clipboard integration still work.
        yield from script(view, "edit.value='synthetic text';true")
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_a)
        yield from keys(window, Gdk.KEY_x)
        assert (yield from script(view, "edit.value")) == "x"
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_z)
        result = yield from script(view, "({value:edit.value,keys})")
        assert result["value"] == "synthetic text", result
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_Shift_L, Gdk.KEY_z)
        assert (yield from script(view, "edit.value")) == "x"
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_a)
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_c)
        clipboard = []

        def copied(source, result):
            clipboard.append(source.read_text_finish(result))

        view.get_clipboard().read_text_async(None, copied)
        yield from wait("copy", lambda: bool(clipboard))
        assert clipboard == ["x"], clipboard
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_x)
        assert (yield from script(view, "edit.value")) == ""
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_v)
        assert (yield from script(view, "edit.value")) == "x"
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_a)
        view.get_clipboard().set("plain paste")
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_Shift_L, Gdk.KEY_v)
        assert (yield from script(view, "edit.value")) == "plain paste"

        # The reserved browser shortcut must continue opening its native dialog.
        yield from keys(window, Gdk.KEY_Control_L, Gdk.KEY_l)
        yield from wait("address dialog", lambda: window.get_visible_dialog() is not None)
        assert window.get_visible_dialog().get_title() == "Open a website"
        window.get_visible_dialog().force_close()
        state["completed"] = True
        window.close()

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
    with tempfile.TemporaryDirectory(prefix="mini-browser-keyboard-") as temporary:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            run(Path(temporary), f"http://127.0.0.1:{server.server_port}/")
            print(
                "PASS: native page shortcuts, key values, modifiers, focus, editing and clipboard"
            )
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
