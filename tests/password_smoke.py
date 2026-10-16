"""Real vault dialogs, CSV import and isolated-world WebKit login filling."""

import csv
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

from mini_browser import autofill
from mini_browser.application import BrowserApplication
from mini_browser.gtk import Adw, Gio, GLib, Gtk

MASTER = "704826"
USERNAME = "generated@example.test"
PASSWORD = "test-only secret, with 'quotes' and \"quotes\" 🔒"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = b"""<!doctype html><meta name="viewport" content="width=device-width">
        <title>Login fixture</title><style>body{padding:24px;font:16px sans-serif}
        input{display:block;width:250px;height:30px;margin:16px 0}</style>
        <h1>Local login fixture</h1><form action="/session" method="post">
        <input type="email" name="username" autocomplete="username">
        <input type="password" name="password" autocomplete="current-password">
        <button>Sign in</button></form><script>
        window.setterCalled=false; window.submitted=false;
        const nativeValue=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
        Object.defineProperty(HTMLInputElement.prototype,'value',{
          ...nativeValue, set(value){window.setterCalled=true; nativeValue.set.call(this,value);}
        });
        document.querySelector('form').addEventListener('submit',e=>{
          e.preventDefault(); window.submitted=true;
        });</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


def button(dialog, label):
    return next(w for w in widgets(dialog) if isinstance(w, Gtk.Button) and w.get_label() == label)


def run():
    with tempfile.TemporaryDirectory(prefix="mini-password-smoke-") as temp:
        root = Path(temp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        csv_path = root / "synthetic-passwords.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "url", "username", "password"])
            writer.writerow(["Fixture", origin + "/login", USERNAME, PASSWORD])
            writer.writerow(["Other site", "https://other.example", "other", "test secret"])
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.PasswordSmoke.Run{os.getpid()}",
        )
        state = {"phase": 0}
        errors = []

        def fail(exc):
            errors.append(type(exc).__name__)
            traceback.print_exc()
            app.quit()

        def exception_hook(kind, value, trace):
            errors.append(kind.__name__)
            traceback.print_exception(kind, value, trace)
            app.quit()

        class Picker:
            def __init__(self, **_):
                pass

            def set_filters(self, _):
                pass

            def open(self, _parent, _cancellable, callback):
                GLib.idle_add(callback, self, object())

            def open_finish(self, _):
                return Gio.File.new_for_path(str(csv_path))

        def js(window, body, completed, *, arguments=None, world=None):
            def finished(view, result):
                try:
                    value = view.call_async_javascript_function_finish(result)
                    completed(json.loads(value.to_json(0)))
                except Exception as exc:
                    fail(exc)

            window.webview.call_async_javascript_function(
                body,
                -1,
                arguments,
                world,
                None,
                None,
                finished,
            )

        def check_filled(result):
            assert result["user"] == USERNAME, state.get("toast", "No fill result")
            assert result["password"] == PASSWORD
            assert result["submitted"] is False
            assert result["setterCalled"] is False  # The page's patched setter was bypassed.
            state["phase"] = 7

        def guard_tests(window):
            def probed(result):
                document = json.loads(result)
                args = {
                    "expectedOrigin": GLib.Variant("s", origin),
                    "expectedDocument": GLib.Variant("s", document["token"]),
                    "username": GLib.Variant("s", USERNAME),
                    "password": GLib.Variant("s", PASSWORD),
                }

                def check_action(result):
                    assert result == "action"
                    args["expectedDocument"] = GLib.Variant("s", "stale-document")

                    def check_stale(result):
                        assert result == "changed"
                        args["expectedDocument"] = GLib.Variant("s", document["token"])
                        args["expectedOrigin"] = GLib.Variant("s", "https://other.example")

                        def check_origin(result):
                            assert result == "changed"
                            state["phase"] = 8

                        js(
                            window,
                            autofill.FILL,
                            check_origin,
                            arguments=GLib.Variant("a{sv}", args),
                            world=autofill.WORLD,
                        )

                    js(
                        window,
                        autofill.FILL,
                        check_stale,
                        arguments=GLib.Variant("a{sv}", args),
                        world=autofill.WORLD,
                    )

                js(
                    window,
                    autofill.FILL,
                    check_action,
                    arguments=GLib.Variant("a{sv}", args),
                    world=autofill.WORLD,
                )

            js(window, autofill.PROBE, probed, world=autofill.WORLD)

        def tick():
            try:
                if not app.get_windows():
                    return GLib.SOURCE_CONTINUE
                window = app.get_windows()[0]
                dialog = window.get_visible_dialog()
                phase = state["phase"]
                if phase == 0:
                    original_toast = window.toast

                    def record_toast(message):
                        state["toast"] = message
                        original_toast(message)

                    window.toast = record_toast
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    assert window.headerbar.get_decoration_layout() == ":close"
                    window.activate_action("win.passwords", None)
                    dialog = window.get_visible_dialog()
                    entries = [w for w in widgets(dialog) if isinstance(w, Gtk.PasswordEntry)]
                    assert len(entries) == 2
                    for entry in entries:
                        entry.set_text(MASTER)
                    button(dialog, "Create vault").emit("clicked")
                    state["phase"] = 1
                elif phase == 1 and dialog and dialog.get_title() == "Passwords":
                    assert app.vault.unlocked
                    with patch("mini_browser.password_dialogs.Gtk.FileDialog", Picker):
                        button(dialog, "Import Chrome password CSV…").emit("clicked")
                    state["phase"] = 2
                elif (
                    phase == 2
                    and app.vault.unlocked
                    and len(app.vault.summaries()) == 2
                    and button(dialog, "Import Chrome password CSV…").get_sensitive()
                ):
                    state["capture_after"] = time.monotonic() + 0.2
                    state["phase"] = 21
                elif phase == 21 and time.monotonic() >= state["capture_after"]:
                    if len(sys.argv) > 1:
                        target = Path(sys.argv[1])
                        target = target.with_name(target.stem + "-passwords.png")
                        snapshot = Gtk.Snapshot.new()
                        state["paintable"].snapshot(
                            snapshot, window.get_width(), window.get_height()
                        )
                        texture = window.get_renderer().render_texture(snapshot.to_node(), None)
                        assert texture.save_to_png(str(target))
                    app.lock_vault()
                    assert not app.vault.unlocked
                    window.activate_action("win.passwords", None)
                    state["phase"] = 3
                elif phase == 3 and dialog and dialog.get_title() == "Unlock password vault":
                    entry = next(w for w in widgets(dialog) if isinstance(w, Gtk.PasswordEntry))
                    entry.set_text("wrong test master")
                    button(dialog, "Unlock").emit("clicked")
                    state["phase"] = 4
                elif phase == 4 and dialog and button(dialog, "Unlock").get_sensitive():
                    labels = [w.get_label() for w in widgets(dialog) if isinstance(w, Gtk.Label)]
                    if not any("Could not unlock" in label for label in labels):
                        return GLib.SOURCE_CONTINUE
                    assert not app.vault.unlocked
                    entry = next(w for w in widgets(dialog) if isinstance(w, Gtk.PasswordEntry))
                    entry.set_text(MASTER)
                    button(dialog, "Unlock").emit("clicked")
                    state["phase"] = 5
                elif phase == 5 and dialog and dialog.get_title() == "Passwords":
                    dialog.force_close()
                    window.load_uri(origin)
                    state["phase"] = 6
                elif phase == 6 and not window.webview.is_loading():
                    if window.webview.get_title() != "Login fixture":
                        return GLib.SOURCE_CONTINUE
                    window.activate_action("win.fill-login", None)
                    state["phase"] = 60
                elif phase == 60 and dialog and dialog.get_title() == "Fill saved login":
                    rows = [w for w in widgets(dialog) if isinstance(w, Adw.ActionRow)]
                    assert len(rows) == 1
                    assert rows[0].get_title() == USERNAME
                    rows[0].emit("activated")
                    state["after_fill"] = time.monotonic() + 0.25
                    state["phase"] = 61
                elif phase == 61 and time.monotonic() >= state["after_fill"]:
                    js(
                        window,
                        """return {user:document.querySelector('[name=username]').value,
                        password:document.querySelector('[name=password]').value,
                        submitted:window.submitted, setterCalled:window.setterCalled};""",
                        check_filled,
                    )
                    state["phase"] = 62
                elif phase == 7:
                    js(
                        window,
                        """document.querySelector('form').action='https://other.example';
                       document.querySelector('[name=password]').value=''; return true;""",
                        lambda _: guard_tests(window),
                    )
                    state["phase"] = 70
                elif phase == 8:

                    def final_check(result):
                        assert result == ""
                        window.activate_action("win.passwords", None)
                        window.activate_action("win.lock-vault", None)
                        assert not app.vault.unlocked
                        assert not window.get_visible_dialog()
                        window.activate_action("win.passwords", None)
                        unlock = window.get_visible_dialog()
                        entry = next(w for w in widgets(unlock) if isinstance(w, Gtk.PasswordEntry))
                        entry.set_text("synthetic unfinished input")
                        window.activate_action("win.lock-vault", None)
                        assert entry.get_text() == ""
                        state["phase"] = 9
                        print(
                            "PASS: vault creation, CSV import, wrong/correct unlock, exact-site "
                            "selection, isolated login fill, no submit, origin/document/form "
                            "guards, manual lock, native header bar"
                        )
                        window.close()

                    js(
                        window,
                        "return document.querySelector('[name=password]').value;",
                        final_check,
                    )
                    state["phase"] = 80
            except Exception as exc:
                fail(exc)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            errors.append(f"Timeout in phase {state['phase']}")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, tick)
        timeout_source = GLib.timeout_add_seconds(30, timeout)
        previous_hook = sys.excepthook
        sys.excepthook = exception_hook
        try:
            app.run(["mini-password-smoke"])
        finally:
            sys.excepthook = previous_hook
            GLib.source_remove(timeout_source)
            server.shutdown()
        if errors or state["phase"] != 9:
            raise SystemExit(
                "; ".join(errors) or f"Password smoke incomplete in phase {state['phase']}"
            )


if __name__ == "__main__":
    run()
