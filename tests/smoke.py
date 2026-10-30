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

from mini_browser import APP_ID, APP_NAME, VERSION
from mini_browser.application import BrowserApplication
from mini_browser.dialogs import address_dialog, library_dialog, shortcuts_dialog
from mini_browser.gtk import Adw, GLib, Gtk, Pango
from mini_browser.window import DEFAULT_HEIGHT, DEFAULT_WIDTH


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if events := self.server.slow_loads.get(self.path):
            started, release = events
            started.set()
            assert release.wait(8), "Loading indicator check did not release the response"
            if self.path == "/fail-loading":
                self.close_connection = True
                return
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
        try:
            self.wfile.write(page)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The stopped-load check deliberately disconnects before the response.

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
        server.slow_loads = {
            path: (threading.Event(), threading.Event())
            for path in ("/loading", "/cancel-loading", "/fail-loading")
        }
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        other_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        other_server.slow_loads = {}
        threading.Thread(target=other_server.serve_forever, daemon=True).start()
        external_base = f"http://127.0.0.1:{other_server.server_port}"
        other_host = f"http://localhost:{server.server_port}"
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

        class FakePermissionRequest:
            def __init__(self, origin):
                self.supported = True
                self.names = ("geolocation",)
                self.origin = origin
                self.cancelled = False
                self.answered = False

            def allow(self):
                self.answered = True
                state["prompt_allowed"] = True

            def deny(self):
                self.answered = True
                state["prompt_allowed"] = False

        def fail(exc):
            errors.append(f"Smoke phase {state['phase']}: {exc or type(exc).__name__}")
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
                assert window.home_button.get_visible() == (
                    window.stack.get_visible_child_name() != "home"
                )
                assert window.home_button.get_sensitive()
                if state["phase"] == 0:
                    assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    assert window.stack.get_visible_child_name() == "home"
                    assert not window.loading_spinner.get_visible()
                    assert not window.layout_button.get_visible()
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    assert window.home_sections.get_visible_child_name() == "apps"
                    window.activate_action("win.home", None)
                    assert app.get_windows() == [window]
                    pages = window.home_sections.get_pages()
                    assert [pages[i].get_title() for i in range(3)] == ["Apps", "History", "Agent"]
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
                    assert not window.home_button.get_visible()
                    assert not window.layout_button.get_visible()
                    assert window.history_stack.get_visible_child_name() == "empty"
                    window.home_sections.set_visible_child_name("apps")
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    assert window.agent_prompt not in widgets(window.app_tiles.get_parent())
                    window.home_sections.set_visible_child_name("agent")
                    assert window.agent_actions_stack.get_visible_child_name() == "empty"
                    assert window.agent_prompt_text.get_lines() == 3
                    assert window.agent_prompt_text.get_ellipsize() == Pango.EllipsizeMode.END
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
                    state["phase"] = "agent-prompt-visible"
                elif state["phase"] == "agent-prompt-visible":
                    valid, bounds = window.copy_agent_prompt_button.compute_bounds(window)
                    assert valid and 0 <= bounds.get_y() < window.get_height()
                    assert window.agent_prompt_text.get_layout().is_ellipsized()
                    assert window.agent_prompt_text.get_layout().get_line_count() == 3
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-agent-prompt.png")))
                    window.home_sections.set_visible_child_name("apps")
                    window.home_entry.set_text(base)
                    window.home_entry.emit("activate")
                    assert window.loading_spinner.get_visible()
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    state["phase"] = 1
                elif state["phase"] == 1 and window.webview.get_title() == "Mini smoke":
                    if window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert window.stack.get_visible_child_name() == "web"
                    assert not window.loading_spinner.get_visible()
                    assert window.layout_button.get_visible()
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
                    assert window.permissions_button.get_visible()
                    window.activate_action("win.permissions", None)
                    site_permissions = window.get_visible_dialog()
                    assert isinstance(site_permissions, Adw.PreferencesDialog)
                    assert site_permissions.get_title() == "Site permissions"
                    assert app.library.permission_records() == []
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-permissions.png")))
                    site_permissions.force_close()
                    window.activate_action("win.permissions-all", None)
                    all_permissions = window.get_visible_dialog()
                    assert isinstance(all_permissions, Adw.PreferencesDialog)
                    assert all_permissions.get_title() == "Permissions"
                    all_permissions.force_close()
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
                    icons = Gtk.IconTheme.get_for_display(window.get_display())
                    assert icons.has_icon(about.get_application_icon()), "About icon is missing"
                    icon = icons.lookup_icon(
                        APP_ID,
                        [],
                        128,
                        window.get_scale_factor(),
                        Gtk.TextDirection.NONE,
                        Gtk.IconLookupFlags.FORCE_REGULAR,
                    )
                    assert icon.get_file().get_basename() == f"{APP_ID}.svg"
                    state["about_shown"] = time.monotonic()
                    state["phase"] = "about"
                elif state["phase"] == "about":
                    if time.monotonic() - state["about_shown"] < 0.5:
                        return GLib.SOURCE_CONTINUE
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-about.png")))
                    about = window.get_visible_dialog()
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
                    assert window._closing and window not in app.get_windows()
                    previous = window
                    window = app.get_windows()[0]
                    assert window is not previous
                    assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    assert window.home_entry.get_text() == ""
                    assert window.home_sections.get_visible_child_name() == "apps"
                    assert window.webview.get_back_forward_list().get_length() == 0
                    assert not window.layout_button.get_visible()
                    assert window.stack.get_visible_child_name() == "home"
                    assert not window.back_button.get_visible()
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
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
                        # Named targets follow the same native popup route.
                        window.webview.evaluate_javascript(
                            "const link = document.querySelector('a'); "
                            "link.addEventListener('click', () => link.target = 'named-page', "
                            "{once:true}); link.click();",
                            -1,
                        )
                        state["phase"] = 26
                        return GLib.SOURCE_CONTINUE
                    if not state.get("external_links_checked"):
                        state["source_window"] = window
                        state["source_uri"] = window.webview.get_uri()
                        state["source_history"] = (
                            window.webview.get_back_forward_list().get_length()
                        )
                        state["external_cases"] = iter(
                            [
                                (other_host + "/second", "link.target = '_blank'; link.click()"),
                                (external_base + "/second", "window.open(link.href, '_blank')"),
                                (
                                    external_base + "/second",
                                    "link.target = 'external'; link.click()",
                                ),
                                (
                                    external_base + "/second",
                                    "link.removeAttribute('target'); "
                                    "const base = document.createElement('base'); "
                                    "base.target = '_blank'; "
                                    "document.head.append(base); link.click()",
                                ),
                                (
                                    external_base + "/second",
                                    "link.target = '_self'; "
                                    "link.style.cssText = 'position:fixed;left:0;top:0;"
                                    "width:200px;height:40px'",
                                    "middle",
                                ),
                            ]
                        )
                        state["phase"] = "external-link"
                        return GLib.SOURCE_CONTINUE
                    window.webview.evaluate_javascript(
                        "document.querySelector('form').requestSubmit()", -1
                    )
                    state["phase"] = 24
                elif state["phase"] == "external-link":
                    window = state["source_window"]
                    case = next(state["external_cases"], None)
                    if case is None:
                        state["external_links_checked"] = True
                        state["phase"] = 23
                        return GLib.SOURCE_CONTINUE
                    uri, script, *button = case
                    state["external_uri"] = uri

                    def activated(view, result):
                        if result.error:
                            fail(result.error)
                        elif button:
                            view.bridge.mouse(view.identifier, 20, 20, 2, 0, 1)
                            view.bridge.mouse(view.identifier, 20, 20, 2, 1, 1)

                    window.webview.evaluate_javascript(
                        "(() => { const link = document.querySelector('a'); "
                        f"link.href = {json.dumps(uri)}; {script}; }})()",
                        -1,
                        callback=activated,
                    )
                    state["phase"] = "external-opened"
                elif state["phase"] == "external-opened":
                    source = state["source_window"]
                    assert source.webview.get_uri() == state["source_uri"]
                    assert (
                        source.webview.get_back_forward_list().get_length()
                        == state["source_history"]
                    )
                    if len(windows) < 2:
                        return GLib.SOURCE_CONTINUE
                    assert len(windows) == 2, "A new-tab link must open exactly one window"
                    other = next(w for w in windows if w is not source)
                    if other.webview.get_title() != "Second page" or other.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    assert other.webview.get_uri() == state["external_uri"]
                    other.close()
                    state["phase"] = "external-link"
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
                    other.home_entry.set_text("Keep this Home search")
                    other.home_sections.set_visible_child_name("history")
                    state["existing_home"] = other
                    state["phase"] = 3
                elif state["phase"] == 3:
                    window = next(w for w in windows if w is not state["existing_home"])
                    # Imported addresses and separate paths must all appear in History.
                    app.library.import_records(
                        [],
                        [(base + f"/imported/{i}", f"Imported page {i}", i, 1) for i in range(250)],
                    )
                    for name in ["Notes", "Calendar", "Mail", "Projects", "Music"]:
                        app.library.record_visit(f"https://{name.lower()}.example", name)
                    app.library.record_visit(base + "/second", "Local app")
                    window.home_button.emit("clicked")
                    assert window._closing and window not in app.get_windows()
                    window = state["existing_home"]
                    assert app.get_windows() == [window]
                    assert not window.layout_button.get_visible()
                    assert window.home_entry.get_text() == "Keep this Home search"
                    assert window.home_sections.get_visible_child_name() == "history"
                    window.home_entry.set_text("")
                    window.home_sections.set_visible_child_name("apps")
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    assert window.stack.get_visible_child_name() == "home"
                    assert window.get_title() == "Mini Browser"
                    assert not window.back_button.get_visible()
                    assert not window.back_button.get_sensitive()
                    assert window.app_tiles.get_child_at_index(3) is not None
                    assert window.app_tiles.get_child_at_index(4) is None
                    state["ready"] = time.monotonic() + 0.2
                    state["phase"] = 31
                elif state["phase"] == 31 and time.monotonic() >= state["ready"]:
                    assert app.get_active_window() is window
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
                    window.set_default_size(900, DEFAULT_HEIGHT)
                    state["ready"] = time.monotonic() + 0.2
                    state["phase"] = 33
                elif state["phase"] == 33 and time.monotonic() >= state["ready"]:
                    if window.get_width() != 900:
                        return GLib.SOURCE_CONTINUE
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-history.png")))
                    for widget in (
                        window.home_tabs,
                        window.home_entry,
                        window.home_sections,
                        window.history_list,
                    ):
                        assert widget.get_allocated_width() == 600
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
                    window.load_uri(base + "/loading")
                    state["phase"] = "loading"
                elif state["phase"] in ("loading", "cancel-loading", "fail-loading"):
                    window = state["size_window"]
                    started, release = server.slow_loads["/" + state["phase"]]
                    if not started.is_set():
                        return GLib.SOURCE_CONTINUE
                    assert window.webview.is_loading()
                    assert window.loading_spinner.get_visible()
                    assert window.loading_spinner.get_mapped()
                    assert window.loading_spinner.get_width() >= 16
                    assert window.loading_spinner.get_height() >= 16
                    assert not window.back_button.get_visible()
                    assert window.loading_spinner.get_tooltip_text() == "Loading page"
                    if state["phase"] == "loading":
                        shown = state.setdefault("spinner_shown", time.monotonic())
                        if time.monotonic() - shown < 0.5:
                            return GLib.SOURCE_CONTINUE
                        painted = Gtk.WidgetPaintable.new(window.loading_spinner)
                        shot = Gtk.Snapshot.new()
                        painted.snapshot(shot, 16, 16)
                        assert shot.to_node() is not None, "Loading spinner renders no pixels"
                        if len(sys.argv) > 1:
                            path = Path(sys.argv[1])
                            state["paintable"] = Gtk.WidgetPaintable.new(window)
                            snapshot(window, str(path.with_name(path.stem + "-loading.png")))
                        release.set()
                        state["phase"] = "loaded"
                    elif state["phase"] == "cancel-loading":
                        window.activate_action("win.stop", None)
                        state["phase"] = "stopped"
                    else:
                        release.set()
                        state["phase"] = "failed"
                elif state["phase"] in ("loaded", "stopped", "failed", "recovered"):
                    window = state["size_window"]
                    if window.webview.is_loading():
                        return GLib.SOURCE_CONTINUE
                    if state["phase"] == "failed":
                        assert window.stack.get_visible_child_name() == "error"
                    assert not window.loading_spinner.get_visible()
                    assert window.back_button.get_mapped()
                    assert window.back_button.get_icon_name() == "go-previous-symbolic"
                    assert window.back_button.get_sensitive()
                    if state["phase"] == "loaded":
                        window.load_uri(base + "/cancel-loading")
                        state["phase"] = "cancel-loading"
                        return GLib.SOURCE_CONTINUE
                    if state["phase"] == "stopped":
                        server.slow_loads["/cancel-loading"][1].set()
                        window.load_uri(base + "/fail-loading")
                        state["phase"] = "fail-loading"
                        return GLib.SOURCE_CONTINUE
                    if state["phase"] == "failed":
                        window.load_uri(base + "/recovered")
                        state["phase"] = "recovered"
                        return GLib.SOURCE_CONTINUE
                    state["permission_origin"] = f"http://127.0.0.1:{server.server_port}"
                    window.activate_action("win.permissions", None)
                    dialog = window.get_visible_dialog()
                    assert dialog.get_title() == "Site permissions"
                    dropdown = next(
                        widget
                        for widget in widgets(dialog)
                        if isinstance(widget, Gtk.DropDown)
                        and "Notifications permission" in (widget.get_tooltip_text() or "")
                    )
                    dropdown.set_selected(1)
                    assert (
                        app.library.permission_decision(state["permission_origin"], "notifications")
                        == "allow"
                    )
                    dialog.force_close()
                    state["permission_started"] = time.monotonic()
                    state["phase"] = "permission-applied"
                    return GLib.SOURCE_CONTINUE
                elif state["phase"] == "permission-applied":
                    window = state["size_window"]
                    if time.monotonic() - state["permission_started"] < 0.5:
                        return GLib.SOURCE_CONTINUE

                    def permission_value(view, result):
                        try:
                            state["permission_value"] = json.loads(
                                view.evaluate_javascript_finish(result).to_json(0)
                            )
                        except Exception as exc:
                            fail(exc)
                        state["phase"] = "permission-checked"

                    window.webview.evaluate_javascript(
                        "Notification.permission",
                        -1,
                        None,
                        None,
                        None,
                        permission_value,
                    )
                    return GLib.SOURCE_CONTINUE
                elif state["phase"] == "permission-checked":
                    window = state["size_window"]
                    assert state["permission_value"] == "granted", state["permission_value"]
                    window.webview.emit(
                        "permission-request",
                        FakePermissionRequest(state["permission_origin"]),
                    )
                    state["phase"] = "prompt-shown"
                    return GLib.SOURCE_CONTINUE
                elif state["phase"] == "prompt-shown":
                    window = state["size_window"]
                    if window._permission_dialog is None:
                        state.setdefault("prompt_wait", time.monotonic())
                        assert time.monotonic() - state["prompt_wait"] < 2, (
                            "permission prompt did not open",
                            window._permission_current,
                            window._permission_queue,
                            app.library.permission_records(state["permission_origin"]),
                        )
                        return GLib.SOURCE_CONTINUE
                    prompt = window._permission_dialog
                    assert isinstance(prompt, Adw.AlertDialog)
                    assert prompt.get_heading() == "Permission request"
                    if len(sys.argv) > 1:
                        path = Path(sys.argv[1])
                        snapshot(window, str(path.with_name(path.stem + "-prompt.png")))
                    prompt.emit("response", "allow")
                    prompt.force_close()
                    state["phase"] = "prompt-answered"
                    return GLib.SOURCE_CONTINUE
                elif state["phase"] == "prompt-answered":
                    window = state["size_window"]
                    assert state.get("prompt_allowed")
                    assert (
                        app.library.permission_decision(state["permission_origin"], "geolocation")
                        == "allow"
                    )
                    print(
                        f"PASS: native window, page load, viewport {state['viewport']}, "
                        "complete history list, 600-pixel Home width, URL app bookmarks, "
                        "Apps/History/Agent tabs, "
                        "settings imports, site permission manager, home entry, "
                        "same-origin links/popups, external links in separate windows, POST, "
                        "Back for links/typed URLs/History API/"
                        "fragments, Home closes apps and creates or focuses Home, "
                        "header restored on media "
                        "URL change, explicit new window, remembered per-host sizes, "
                        "loading indicator and Back restoration after completion/stop/failure, "
                        "close"
                    )
                    state["phase"] = "done"
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
        timeout_source = GLib.timeout_add_seconds(45, timeout)
        try:
            app.run(["mini-browser-smoke"])
        finally:
            GLib.source_remove(timeout_source)
            for _started, release in server.slow_loads.values():
                release.set()
            server.shutdown()
            other_server.shutdown()
        if errors:
            raise SystemExit("; ".join(errors))
        if state["phase"] != "done":
            raise SystemExit("Smoke test exited before completion")


if __name__ == "__main__":
    run()
