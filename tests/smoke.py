"""Exercise real GTK/Chromium using only generated data and a local HTTP server."""

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

from mini_browser import APP_NAME, VERSION
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
            'Open another page</a><form action="/submitted" method="post" target="_blank">'
            '<input name="fixture" value="synthetic"><button>Submit</button></form>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        assert self.path == "/submitted" and body == b"fixture=synthetic"
        page = b"<title>Submitted page</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
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
            discover_devices=False,
            allow_autoplay=True,
            silent=True,
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
                assert window.home_button.get_visible() and window.home_button.get_sensitive()
                if state["phase"] == 0:
                    assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    assert window.stack.get_visible_child_name() == "home"
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    assert window.home_sections.get_visible_child_name() == "apps"
                    pages = window.home_sections.get_pages()
                    assert [pages[i].get_title() for i in range(2)] == ["Apps", "History"]
                    assert (
                        window.app_tiles.get_child_at_index(0).get_child() == window.deepseek_button
                    )
                    assert window.app_tiles.get_child_at_index(2) is not None
                    assert window.app_tiles.get_child_at_index(3) is None
                    assert not any(
                        isinstance(widget, Gtk.Label)
                        and widget.get_text() in {"Devices", "Script apps", "Bookmarked apps"}
                        for widget in widgets(window.app_tiles.get_parent())
                    )
                    assert not window.lookup_action("bookmark").get_enabled()
                    window.home_sections.set_visible_child_name("history")
                    assert window.history_stack.get_visible_child_name() == "empty"
                    window.home_sections.set_visible_child_name("apps")
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    assert window.agent_prompt.get_parent().get_last_child() == window.agent_prompt
                    state["visible_agent_prompt"] = window.agent_prompt_text.get_text()
                    window.copy_agent_prompt_button.grab_focus()
                    window.copy_agent_prompt_button.emit("clicked")

                    def copied(clipboard, result):
                        try:
                            state["agent_prompt"] = clipboard.read_text_finish(result)
                        except Exception as error:
                            fail(error)

                    window.get_clipboard().read_text_async(None, copied)
                    state["phase"] = "agent-prompt"
                elif state["phase"] == "agent-prompt":
                    if "agent_prompt" not in state:
                        return GLib.SOURCE_CONTINUE
                    assert state["agent_prompt"] == state["visible_agent_prompt"]
                    adjustment = window.home_sections.get_child_by_name("apps").get_vadjustment()
                    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                    state["phase"] = "agent-prompt-visible"
                elif state["phase"] == "agent-prompt-visible":
                    valid, bounds = window.copy_agent_prompt_button.compute_bounds(window)
                    assert valid and 0 <= bounds.get_y() < window.get_height()
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-agent-prompt.png")))
                    window.home_sections.get_child_by_name("apps").get_vadjustment().set_value(0)
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
                        notified.assert_called_once_with("App bookmarked")
                    assert len(app.library.search("bookmarks")) == 1
                    assert app.library.search("bookmarks")[0]["url"] == base + "/"
                    menu = window.menu_button.get_menu_model()
                    assert any(
                        menu.get_item_attribute_value(i, "action", None).get_string()
                        == "win.bookmark"
                        for i in range(menu.get_n_items())
                    )
                    # Verify each temporary dialog can be constructed in a narrow window.
                    for callback in [address_dialog, library_dialog, shortcuts_dialog]:
                        callback(window)
                        window.get_visible_dialog().force_close()
                    window.menu_button.popup()
                    assert window.menu_button.get_popover().get_visible()
                    window.menu_button.popdown()
                    window.activate_action("win.about", None)
                    about = window.get_visible_dialog()
                    assert isinstance(about, Adw.AboutDialog)
                    assert about.get_application_name() == APP_NAME
                    assert about.get_version() == VERSION
                    about.force_close()
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
                            state["initial_uri"] = view.get_uri()
                            view.evaluate_javascript("history.pushState({}, '', '/spa')", -1)
                            state["phase"] = 11
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
                elif state["phase"] == 11 and window.webview.get_uri() == base + "/spa":
                    assert window.webview.can_go_back()
                    assert window.back_button.get_visible() and window.back_button.get_mapped()
                    assert window.back_button.get_sensitive()
                    assert window.back_button.get_icon_name() == "go-previous-symbolic"
                    window.back_button.emit("clicked")
                    state["phase"] = 12
                elif state["phase"] == 12 and window.webview.get_uri() == state["initial_uri"]:
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    window.webview.evaluate_javascript("location.hash = 'section'", -1)
                    state["phase"] = 13
                elif (
                    state["phase"] == 13
                    and window.webview.get_uri() == state["initial_uri"] + "#section"
                ):
                    assert window.back_button.get_sensitive()
                    window.back_button.emit("clicked")
                    state["phase"] = 14
                elif state["phase"] == 14 and window.webview.get_uri() == state["initial_uri"]:
                    address_dialog(window)
                    dialog = window.get_visible_dialog()
                    entry = next(
                        widget for widget in widgets(dialog) if isinstance(widget, Gtk.Entry)
                    )
                    entry.set_text(base + "/typed")
                    entry.emit("activate")
                    state["phase"] = 15
                elif (
                    state["phase"] == 15
                    and window.webview.get_uri() == base + "/typed"
                    and not window.webview.is_loading()
                ):
                    assert window.back_button.get_sensitive()
                    window.back_button.emit("clicked")
                    state["phase"] = 16
                elif (
                    state["phase"] == 16
                    and window.webview.get_uri() == state["initial_uri"]
                    and not window.webview.is_loading()
                ):
                    window.webview.evaluate_javascript(
                        "globalThis.testAudio = new AudioContext(); "
                        "const oscillator = testAudio.createOscillator(); "
                        "oscillator.connect(testAudio.destination); oscillator.start(); "
                        "testAudio.resume();",
                        -1,
                    )
                    state["phase"] = 17
                elif state["phase"] == 17 and window.lookup_action("audio-player").get_enabled():
                    window.activate_action("win.audio-player", None)
                    assert window.audio_mode
                    assert not window.toolbar.get_reveal_top_bars()
                    window.webview.evaluate_javascript(
                        "history.pushState({}, '', '/media-route')", -1
                    )
                    state["phase"] = 18
                elif state["phase"] == 18 and window.webview.get_uri() == base + "/media-route":
                    assert not window.audio_mode, "URL changes must reveal the browsing header"
                    assert window.toolbar.get_reveal_top_bars()
                    assert window.back_button.get_sensitive()
                    window.webview.evaluate_javascript("testAudio.close()", -1)
                    window.back_button.emit("clicked")
                    state["phase"] = 19
                elif state["phase"] == 19 and window.webview.get_uri() == state["initial_uri"]:
                    window.webview.evaluate_javascript("document.querySelector('a').click()", -1)
                    state["phase"] = 2
                elif state["phase"] == 2:
                    if window.webview.get_title() != "Second page" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert len(windows) == 1
                    assert window.back_button.get_sensitive()
                    assert len(app.library.search("history")) == 3
                    window.back_button.emit("clicked")
                    state["phase"] = 21
                elif state["phase"] == 21:
                    if window.webview.get_title() != "Mini smoke" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    assert window.lookup_action("forward").get_enabled()
                    window.home_button.emit("clicked")
                    assert window.stack.get_visible_child_name() == "home"
                    assert not window.back_button.get_visible()
                    window.home_entry.set_text(base)
                    window.home_entry.emit("activate")
                    state["phase"] = 211
                elif state["phase"] == 211:
                    if window.webview.get_title() != "Mini smoke" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    window.webview.evaluate_javascript("window.open('/second', '_blank')", -1)
                    state["phase"] = 22
                elif state["phase"] == 22:
                    if window.webview.get_title() != "Second page" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert len(windows) == 1
                    window.back_button.emit("clicked")
                    state["phase"] = 23
                elif state["phase"] == 23:
                    if window.webview.get_title() != "Mini smoke" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    if not state.get("policy_link_checked"):
                        # A site can change the target after our capture listener.
                        # Exercise Chromium's new-window policy fallback as well.
                        window.webview.evaluate_javascript(
                            "const link = document.querySelector('a'); "
                            "link.addEventListener('click', () => link.target = '_blank', "
                            "{once:true}); link.click();",
                            -1,
                        )
                        state["phase"] = 26
                        return GLib.SOURCE_CONTINUE
                    window.webview.evaluate_javascript(
                        "document.querySelector('form').requestSubmit()", -1
                    )
                    state["phase"] = 24
                elif state["phase"] == 26:
                    if window.webview.get_title() != "Second page" or window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert len(windows) == 1
                    state["policy_link_checked"] = True
                    window.back_button.emit("clicked")
                    state["phase"] = 23
                elif state["phase"] == 24:
                    if (
                        window.webview.get_title() != "Submitted page"
                        or window.webview.is_loading()
                    ):
                        return GLib.SOURCE_CONTINUE
                    assert len(windows) == 1
                    assert window.webview.get_uri() == base + "/submitted"
                    # Explicit new-window requests remain available to the user.
                    menu = window.menu_button.get_menu_model()
                    index = next(
                        index
                        for index in range(menu.get_n_items())
                        if menu.get_item_attribute_value(index, "label", None).get_string()
                        == "New Window"
                    )
                    action = menu.get_item_attribute_value(index, "action", None).get_string()
                    assert action == "app.new-window"
                    window.activate_action(action, None)
                    state["phase"] = 25
                elif state["phase"] == 25:
                    assert len(windows) == 2
                    other = next(w for w in windows if w.stack.get_visible_child_name() == "home")
                    assert other.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    other.close()
                    state["phase"] = 3
                elif state["phase"] == 3 and len(windows) == 1:
                    # Imported addresses and separate paths must all appear in History.
                    app.library.import_records(
                        [],
                        [(base + f"/imported/{i}", f"Imported page {i}", i, 1) for i in range(250)],
                    )
                    for name in ["Notes", "Calendar", "Mail", "Projects", "Music"]:
                        app.library.record_visit(f"https://{name.lower()}.example", name)
                    app.library.record_visit(base + "/second", "Local app")
                    window.home_button.emit("clicked")
                    assert window.stack.get_visible_child_name() == "home"
                    assert window.get_title() == "Mini Browser"
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    assert window.app_tiles.get_child_at_index(3) is not None
                    assert window.app_tiles.get_child_at_index(4) is None
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
                    window.home_sections.set_visible_child_name("history")
                    assert window.history_model.get_n_items() > 250
                    assert window.history_model.get_n_items() == len(
                        app.library.search("history", limit=None)
                    )
                    state["ready"] = time.monotonic() + 0.2
                    state["phase"] = 33
                elif state["phase"] == 33 and time.monotonic() >= state["ready"]:
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-history.png")))
                    assert window.history_list.get_width() == window.get_width()
                    row = next(
                        child
                        for child in widgets(window.history_list)
                        if isinstance(child, Gtk.Label) and child.get_label() == "Local app"
                    )
                    assert row.get_width() > 100
                    index = next(
                        i
                        for i in range(window.history_model.get_n_items())
                        if window.history_model[i].record["url"] == base + "/second"
                    )
                    window.history_list.emit("activate", index)
                    state["phase"] = 4
                elif (
                    state["phase"] == 4
                    and not window.webview.is_loading()
                    and window.webview.get_uri() == base + "/second"
                ):
                    assert window.stack.get_visible_child_name() == "web"
                    state["size_window"] = window
                    window.set_default_size(620, 690)
                    state["phase"] = 41
                elif state["phase"] == 41:
                    window = state["size_window"]
                    if (window.get_width(), window.get_height()) != (620, 690):
                        return GLib.SOURCE_CONTINUE
                    window.load_uri(base + "/resized")
                    state["phase"] = 42
                elif state["phase"] == 42:
                    window = state["size_window"]
                    if window.webview.is_loading() or window.webview.get_uri() != base + "/resized":
                        return GLib.SOURCE_CONTINUE
                    assert window.get_default_size() == (620, 690)
                    other_base = f"http://localhost:{server.server_port}"
                    state["other_base"] = other_base
                    state["size_window"] = app.new_window(other_base + "/other-host")
                    window.close()
                    state["phase"] = 43
                elif state["phase"] == 43:
                    window = state["size_window"]
                    if window.webview.is_loading() or window._layout_source:
                        return GLib.SOURCE_CONTINUE
                    assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    window.set_default_size(730, 620)
                    state["phase"] = 44
                elif state["phase"] == 44:
                    window = state["size_window"]
                    if (window.get_width(), window.get_height()) != (730, 620):
                        return GLib.SOURCE_CONTINUE
                    window.load_uri(base + "/reopened")
                    state["phase"] = 45
                elif state["phase"] == 45:
                    window = state["size_window"]
                    if window.webview.is_loading() or window._layout_source:
                        return GLib.SOURCE_CONTINUE
                    assert window.get_default_size() == (620, 690)
                    assert (window.get_width(), window.get_height()) == (620, 690)
                    window.load_uri(state["other_base"] + "/again")
                    state["phase"] = 46
                elif state["phase"] == 46:
                    window = state["size_window"]
                    if window.webview.is_loading() or window._layout_source:
                        return GLib.SOURCE_CONTINUE
                    assert window.get_default_size() == (730, 620)
                    assert (window.get_width(), window.get_height()) == (730, 620)
                    print(
                        f"PASS: native window, page load, viewport {state['viewport']}, "
                        "complete history list, URL app bookmarks, Apps/History tabs, "
                        "settings imports, home entry, "
                        "same-window links/popups/POST, Back for links/typed URLs/History API/"
                        "fragments, persistent Home button, header restored on media "
                        "URL change, explicit new window, remembered per-host sizes, close"
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
        if state["phase"] != 46:
            raise SystemExit("Smoke test exited before completion")


if __name__ == "__main__":
    run()
