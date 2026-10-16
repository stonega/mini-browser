"""Exercise real GTK/WebKit using only generated data and a local HTTP server."""

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
from mini_browser.dialogs import address_dialog, library_dialog, shortcuts_dialog
from mini_browser.gtk import Adw, GLib, Gtk
from mini_browser.window import DEFAULT_HEIGHT, DEFAULT_WIDTH


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        second = self.path == "/second"
        page = (
            '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{'Second page' if second else 'Mini smoke'}</title>"
            "<style>body{font:18px sans-serif;background:#f6f5f4;color:#241f31;"
            "margin:0;padding:72px 24px}h1{font-size:34px}a{color:#1c71d8}</style>"
            f"<h1>{'Another little window.' if second else 'A little window to the web.'}</h1>"
            '<p>Native GNOME. One page per window.</p><a href="/second" target="_blank">'
            "Open another page</a>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *_):
        pass


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-smoke-") as temp:
        root = Path(temp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.Smoke.Run{os.getpid()}",
        )
        errors = []
        state = {"phase": 0}

        def fail(exc):
            errors.append(str(exc))
            traceback.print_exc()
            app.quit()

        def snapshot(window, filename):
            # Render only this app window, without desktop/private content.
            paintable = state["paintable"]
            shot = Gtk.Snapshot.new()
            paintable.snapshot(shot, window.get_width(), window.get_height())
            node = shot.to_node()
            texture = window.get_renderer().render_texture(node, None)
            assert texture.save_to_png(filename)

        def tick():
            try:
                windows = app.get_windows()
                if not windows:
                    return GLib.SOURCE_CONTINUE
                window = windows[-1]
                if state["phase"] == 0:
                    assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    assert window.stack.get_visible_child_name() == "home"
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    assert window.recent_empty.get_visible()
                    window.home_entry.set_text(base)
                    window.home_entry.emit("activate")
                    state["phase"] = 1
                elif state["phase"] == 1 and window.webview.get_title() == "Mini smoke":
                    if window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert window.stack.get_visible_child_name() == "web"
                    assert len(app.library.search("history")) == 1
                    assert len(app.library.recent_apps()) == 1
                    if len(sys.argv) > 1:
                        snapshot(window, sys.argv[1])
                    with patch.object(window, "toast") as notified:
                        window.activate_action("win.bookmark", None)
                        notified.assert_called_once_with("Bookmark saved")
                    assert len(app.library.search("bookmarks")) == 1
                    # Verify each temporary dialog can be constructed in a narrow window.
                    for callback in [address_dialog, library_dialog, shortcuts_dialog]:
                        callback(window)
                        window.get_visible_dialog().force_close()
                    with patch("mini_browser.dialogs.discover_profiles", return_value=[]):
                        window.activate_action("win.settings", None)
                        settings = window.get_visible_dialog()
                        assert settings.get_title() == "Settings"
                        row = next(
                            widget
                            for widget in widgets(settings)
                            if isinstance(widget, Adw.ActionRow)
                            and widget.get_title() == "Import bookmarks and history"
                        )
                        row.emit("activated")
                        dialog = window.get_visible_dialog()
                        assert dialog.get_title() == "Import from Chrome"
                        dialog.force_close()
                    window.activate_action("win.settings", None)
                    settings = window.get_visible_dialog()
                    row = next(
                        widget
                        for widget in widgets(settings)
                        if isinstance(widget, Adw.ActionRow)
                        and widget.get_title() == "Import passwords"
                    )
                    with patch("mini_browser.window.passwords_dialog") as open_passwords:
                        row.emit("activated")
                        open_passwords.assert_called_once_with(window, import_now=True)

                    def evaluated(view, result):
                        try:
                            dimensions = json.loads(
                                view.evaluate_javascript_finish(result).to_json(0)
                            )
                            assert 300 <= dimensions["width"] <= 500, dimensions
                            state["viewport"] = dimensions
                            view.get_settings().set_property(
                                "javascript-can-open-windows-automatically",
                                True,
                            )
                            view.evaluate_javascript("window.open('/second', '_blank')", -1)
                            state["phase"] = 2
                        except Exception as exc:
                            fail(exc)

                    window.webview.evaluate_javascript(
                        "({width:innerWidth,height:innerHeight})",
                        -1,
                        None,
                        None,
                        None,
                        evaluated,
                    )
                    state["phase"] = 10
                elif state["phase"] == 2:
                    if len(windows) != 2:
                        return GLib.SOURCE_CONTINUE
                    second = next(w for w in windows if w.webview.get_title() == "Second page")
                    if second.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert second.webview != window.webview
                    assert second.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    assert len(app.library.search("history")) == 2
                    second.activate_action("win.close", None)
                    state["phase"] = 3
                elif state["phase"] == 3 and len(windows) == 1:
                    # Synthetic app history also exercises the complete home grid.
                    for name in ["Notes", "Calendar", "Mail", "Projects", "Music"]:
                        app.library.record_visit(f"https://{name.lower()}.example", name)
                    app.library.record_visit(base + "/second", "Local app")
                    window.activate_action("win.home", None)
                    assert window.stack.get_visible_child_name() == "home"
                    assert window.get_title() == "Mini Browser"
                    assert not window.recent_empty.get_visible()
                    state["ready"] = time.monotonic() + 0.2
                    state["phase"] = 31
                elif state["phase"] == 31 and time.monotonic() >= state["ready"]:
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-home.png")))
                    window.activate_action("win.settings", None)
                    state["ready"] = time.monotonic() + 0.2
                    state["phase"] = 32
                elif state["phase"] == 32 and time.monotonic() >= state["ready"]:
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-settings.png")))
                    window.get_visible_dialog().force_close()
                    window.recent_tiles.get_first_child().get_child().emit("clicked")
                    state["phase"] = 4
                elif (
                    state["phase"] == 4
                    and not window.webview.is_loading()
                    and window.webview.get_uri() == base + "/second"
                ):
                    assert window.stack.get_visible_child_name() == "web"
                    print(
                        f"PASS: native window, page load, viewport {state['viewport']}, "
                        "history, bookmark, settings imports, home entry, recent apps, popup, close"
                    )
                    window.close()
                    return GLib.SOURCE_REMOVE
            except StopIteration:
                return GLib.SOURCE_CONTINUE
            except Exception as exc:
                fail(exc)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            errors.append(f"Smoke test timed out in phase {state['phase']}")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, tick)
        timeout_source = GLib.timeout_add_seconds(25, timeout)
        try:
            app.run(["mini-browser-smoke"])
        finally:
            GLib.source_remove(timeout_source)
            server.shutdown()
        if errors:
            raise SystemExit("; ".join(errors))
        if state["phase"] != 4:
            raise SystemExit("Smoke test exited before completion")


if __name__ == "__main__":
    run()
