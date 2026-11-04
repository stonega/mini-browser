"""Native extension controls and real MV3 execution across isolated process restarts."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from extension_fixture import signed_package

from mini_browser import extension_page
from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib, Gtk


class Handler(BaseHTTPRequestHandler):
    blocked_requests = 0

    def do_GET(self):
        if self.path == "/blocked-by-extension":
            type(self).blocked_requests += 1
        body = b"<!doctype html><title>Extension fixture</title><body>Fixture</body>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def fixtures(root):
    content = root / "Content fixture"
    content.mkdir()
    (content / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Content fixture",
                "version": "1.0",
                "permissions": ["storage", "clipboardWrite"],
                "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["content.js"]}],
                "background": {"service_worker": "background.js"},
                "action": {"default_popup": "popup.html"},
            }
        )
    )
    (content / "content.js").write_text("""
        chrome.runtime.sendMessage({fixture: true}, reply => {
            document.title = reply && reply.value === 'synthetic' ?
                'Extension active' : 'Extension messaging failed';
        });
        document.addEventListener('fixture-open-window', () => {
            chrome.runtime.sendMessage({openWindow: true}, () => {
                void chrome.runtime.lastError;
            });
        });
    """)
    (content / "background.js").write_text("""
        chrome.runtime.onInstalled.addListener(() => {
            chrome.windows.create({url: chrome.runtime.getURL('welcome.html'),
                type: 'popup'}, () => {
                    void chrome.runtime.lastError;
                });
        });
        chrome.runtime.onMessage.addListener((message, sender, reply) => {
            if (message.openWindow) {
                chrome.windows.create({url: chrome.runtime.getURL('notification.html'),
                    type: 'popup'}, () => {
                        void chrome.runtime.lastError; reply({opened: true});
                    });
                return true;
            }
            chrome.storage.local.set({fixture: 'synthetic'}, () => {
                chrome.storage.local.get('fixture', data => reply({value: data.fixture}));
            });
            return true;
        });
    """)
    (content / "welcome.html").write_text("<!doctype html><title>Extension welcome</title>")
    (content / "notification.html").write_text(
        "<!doctype html><title>Extension notification</title><body>Generated notification</body>"
    )
    (content / "popup.html").write_text(
        '<!doctype html><title>Popup loading</title><script src="popup.js" defer></script>'
        "<style>button{position:absolute;left:20px;width:220px;height:48px}"
        "#message{top:20px}#notification{top:90px}#copy{top:160px}</style>"
        '<body><button id="message">Message background</button>'
        '<button id="notification">Open notification</button>'
        '<button id="copy">Copy synthetic text</button></body>'
    )
    (content / "popup.js").write_text("""
        chrome.runtime.sendMessage({fixture: true}, reply => {
            document.title = reply && reply.value === 'synthetic' ?
                'Extension popup ready' : 'Popup messaging failed';
        });
        message.onclick = () => chrome.runtime.sendMessage({fixture: true}, reply => {
            document.title = reply.value === 'synthetic' ? 'Popup button worked' : 'Failed';
        });
        notification.onclick = () => chrome.runtime.sendMessage({openWindow: true});
        copy.onclick = () => navigator.clipboard.writeText('Synthetic extension clipboard')
            .then(() => document.title = 'Popup copied')
            .catch(error => {
                document.title = 'Popup copy error:' + error.name + ':' + error.message;
            });
    """)
    network = root / "Network fixture"
    network.mkdir()
    (network / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Network fixture",
                "version": "1.0",
                "permissions": ["declarativeNetRequest"],
                "host_permissions": ["http://127.0.0.1/*"],
                "declarative_net_request": {
                    "rule_resources": [{"id": "fixture", "enabled": True, "path": "rules.json"}]
                },
            }
        )
    )
    (network / "rules.json").write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "priority": 1,
                    "action": {"type": "block"},
                    "condition": {
                        "urlFilter": "blocked-by-extension",
                        "resourceTypes": ["xmlhttprequest"],
                    },
                }
            ]
        )
    )


def descendants(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def tile(grid, index):
    child = grid.get_child_at_index(index)
    assert child is not None
    return child.get_child()


def phase(root, number, screenshot=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = BrowserApplication(
        data_dir=root / "data",
        cache_dir=root / "cache",
        discover_devices=False,
        silent=True,
        application_id=f"io.github.stone.MiniBrowser.ExtensionSmoke.Run{os.getpid()}",
    )
    errors, state, extension_requests = [], {}, []

    def observe_extension_requests(*_):
        app.network_session.connect(
            "extension-window", lambda _session, uri: extension_requests.append(uri)
        )

    app.connect("startup", observe_extension_requests)

    def wait(predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            assert time.monotonic() < deadline, f"Extension phase {number} timed out"
            yield

    def command(view, method, params=None):
        result = []
        view.command(method, params, lambda data, error: result.append((data, error)))
        yield from wait(lambda: bool(result))
        assert result[0][1] is None, method
        return result[0][0]

    def click_widget(widget, x, y):
        window = widget.get_root()
        path = f"{app.get_dbus_object_path()}/window/{window.get_id()}"
        deadline = time.monotonic() + 5
        while True:
            valid, bounds = widget.compute_bounds(window)
            assert valid
            completed = []

            def finished(connection, result, replies=completed):
                try:
                    connection.call_finish(result)
                    replies.append(None)
                except GLib.Error as error:
                    replies.append(error)

            app.get_dbus_connection().call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "Click",
                GLib.Variant("(oddu)", (path, bounds.get_x() + x, bounds.get_y() + y, 1)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                finished,
            )
            yield from wait(lambda replies=completed: bool(replies))
            if completed[0] is None:
                return
            # GTK may map before Mutter has committed the new window's frame.
            if time.monotonic() >= deadline:
                raise completed[0]
            yield

    def click(popup, x, y):
        yield from click_widget(popup.view, x, y)

    def market_tab(page):
        label = next(
            child
            for child in descendants(page.tabs)
            if isinstance(child, Gtk.Label) and child.get_label() == "Market"
        )
        button = label.get_parent()
        while not isinstance(button, Gtk.Button):
            button = button.get_parent()
        yield from wait(lambda: button.get_mapped() and button.get_width() > 0)
        for _attempt in range(3):
            yield from click_widget(button, button.get_width() / 2, button.get_height() / 2)
            deadline = time.monotonic() + 1
            while (
                page.sections.get_visible_child_name() != "market" and time.monotonic() < deadline
            ):
                yield
            if page.sections.get_visible_child_name() == "market":
                return
        raise AssertionError("Native Market tab clicks did not switch sections")

    def market_button(page, label):
        return next(
            child
            for child in descendants(tile(page.market_grid, 0))
            if isinstance(child, Gtk.Button) and child.get_label() == label
        )

    def install_market(manager, page, window):
        identifier, package = signed_package(
            manifest={
                "manifest_version": 3,
                "name": "Market fixture",
                "version": "1.0",
                "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["content.js"]}],
                "action": {"default_popup": "popup.html"},
            },
            files=[
                ("content.js", "document.title = 'Market installed active';"),
                ("popup.html", "<!doctype html><title>Signed market popup</title>"),
            ],
        )
        entry = {**extension_page.MARKET_EXTENSIONS[0], "id": identifier}
        gate = threading.Event()
        attempts = []

        def download(_url, destination, progress, _cancelled):
            attempts.append(True)
            if len(attempts) == 1:
                raise ValueError("Synthetic download failed. Try again.")
            progress("Downloading 50%")
            assert gate.wait(10), "Installer UI test stalled"
            destination.write_bytes(package)

        with (
            patch.object(extension_page, "MARKET_EXTENSIONS", (entry,)),
            patch("mini_browser.extension_installer.download_package", side_effect=download),
        ):
            page.sections.set_visible_child_name("market")
            page.refresh()
            market_button(page, "Install").emit("clicked")
            yield from wait(lambda: not app.extension_install_state[identifier]["busy"])
            assert app.extension_install_state[identifier]["error"]
            assert app.extensions.all() == []
            install = market_button(page, "Install")
            yield from wait(lambda: install.get_mapped())
            yield from click_widget(install, install.get_width() / 2, install.get_height() / 2)
            yield from wait(lambda: app.extension_install_state[identifier]["busy"])
            assert not market_button(page, "Installing…").get_sensitive()
            # Duplicate activation cannot start a second worker.
            page._install_market(None, entry)
            manager.close()
            yield from wait(lambda: app.extensions_window is None)
            window.activate_action("win.extensions", None)
            manager = app.extensions_window
            page = manager.extensions_page
            page.sections.set_visible_child_name("market")
            assert not market_button(page, "Installing…").get_sensitive()
            gate.set()
            yield from wait(lambda: not app.extension_install_state[identifier]["busy"])
            assert not app.extension_install_state[identifier]["error"]
            assert len(attempts) == 2
            records = app.extensions.all()
            assert len(records) == 1 and records[0]["name"] == "Market fixture"
            (root / "market-install.json").write_text(
                json.dumps({"id": identifier, "path": records[0]["path"]})
            )
            assert page.restart_notice.get_visible()
            market_button(page, "Installed").emit("clicked")
            assert page.sections.get_visible_child_name() == "installed"
            remove = next(
                child
                for child in descendants(tile(page.installed_grid, 0))
                if isinstance(child, Gtk.Button) and child.get_icon_name() == "user-trash-symbolic"
            )
            remove.emit("clicked")
            assert app.extensions.all() == []
            assert Path(records[0]["path"]).is_dir()
            page.sections.set_visible_child_name("market")
            assert market_button(page, "Install").get_sensitive()
        page.sections.set_visible_child_name("installed")
        page.refresh()
        return manager, page

    def scenario():
        yield from wait(lambda: bool(app.get_windows()))
        window = next(w for w in app.get_windows() if hasattr(w, "webview"))
        view = window.webview
        yield from wait(lambda: window.get_mapped() and view.prepared)
        if number == 2:
            # Wait for the real onInstalled request, rather than assuming the
            # extension worker has already started when Home becomes ready.
            yield from wait(
                lambda: any(uri.endswith("/welcome.html") for uri in extension_requests)
            )
        assert app.extensions_window is None
        assert not app.extension_popup_windows, "An extension opened a window at startup"
        assert app.get_windows() == [window]
        pages = window.home_sections.get_pages()
        assert [pages[i].get_title() for i in range(pages.get_n_items())] == [
            "Apps",
            "History",
            "Agent",
        ]
        assert not hasattr(window, "extensions_page")
        assert window.add_app_button.get_visible()
        assert not window.home_button.get_visible()
        assert window.home_sections.get_visible_child_name() == "apps"
        assert window.get_width() == 420
        window.menu_button.popup()
        popover = window.menu_button.get_popover()
        yield from wait(lambda: popover.get_mapped())
        label = next(
            widget
            for widget in descendants(popover)
            if isinstance(widget, Gtk.Label) and widget.get_label() == "Extensions"
        )
        menu_item = label.get_parent()
        while menu_item.get_css_name() != "modelbutton":
            menu_item = menu_item.get_parent()
        assert menu_item.activate()
        yield from wait(lambda: app.extensions_window is not None)
        manager = app.extensions_window
        page = manager.extensions_page
        state["paintable"] = Gtk.WidgetPaintable.new(manager)
        yield from wait(lambda: manager.get_mapped())
        assert manager is not window and not hasattr(manager, "webview")
        assert window.stack.get_visible_child_name() == "home"
        sections = page.sections.get_pages()
        assert [sections[i].get_title() for i in range(sections.get_n_items())] == [
            "Installed",
            "Market",
        ]
        assert page.sections.get_visible_child_name() == "installed"
        assert page.load_button.get_visible()
        yield from market_tab(page)
        assert not page.load_button.get_visible()
        page.search_entry.set_text("mEtAmAsK")
        assert page.market_grid.get_child_at_index(1) is None
        official = next(
            child
            for child in descendants(tile(page.market_grid, 0))
            if isinstance(child, Gtk.Button) and child.get_label() == "Official page"
        )
        with patch.object(app, "new_window") as opened:
            official.emit("clicked")
            opened.assert_called_once_with("https://metamask.com/download")
        page.search_entry.set_text("no-such-project")
        assert page.market_empty.get_visible()
        page.search_entry.emit("icon-press", Gtk.EntryIconPosition.SECONDARY)
        assert page.search_entry.get_text() == ""
        assert page.market_grid.get_child_at_index(2) is not None
        assert (
            sum(
                isinstance(child, Gtk.Button) and child.get_label() == "Install"
                for child in descendants(page.market_grid)
            )
            == 3
        )
        page.sections.set_visible_child_name("installed")
        assert page.load_button.get_visible()
        content = str(root / "Content fixture")
        network = str(root / "Network fixture")
        if number == 4:
            record = json.loads((root / "market-install.json").read_text())
            assert app.extension_launchers[record["path"]]["id"] == record["id"]
            window.load_uri(f"http://127.0.0.1:{server.server_port}/")
            yield from wait(
                lambda: view.get_title() == "Market installed active" and not view.loading
            )
            popup = app.open_extension_popup(record["path"])
            yield from wait(lambda: popup.view.get_title() == "Signed market popup")
            result = yield from command(
                popup.view,
                "Runtime.evaluate",
                {"expression": "chrome.runtime.id", "returnByValue": True},
            )
            assert result["result"]["value"] == record["id"]
            app.extensions.remove(record["path"])
            return
        if number == 1:
            assert page.empty.get_visible()
            manager, page = yield from install_market(manager, page, window)
            state["paintable"] = Gtk.WidgetPaintable.new(manager)
            page.add_folder(root / "missing")
            yield from wait(lambda: not page.loading)
            assert page.status.get_visible()
            assert app.extensions.all() == []
            for path in (content, content, network):
                page.add_folder(path)
                yield from wait(lambda: not page.loading)
            assert len(app.extensions.all()) == 2
            assert page.restart_notice.get_visible()
            assert not page.empty.get_visible()
            page.search_entry.set_text("NeTwOrK")
            assert page.installed_grid.get_child_at_index(1) is None
            assert not page.empty.get_visible()
            page.search_entry.set_text("no-such-extension")
            assert page.empty.get_visible()
            page.search_entry.emit("icon-press", Gtk.EntryIconPosition.SECONDARY)
            assert page.installed_grid.get_child_at_index(1) is not None
            row = tile(page.installed_grid, 0)
            switch = next(child for child in descendants(row) if isinstance(child, Gtk.Switch))
            switch.set_active(False)
            manifest = Path(content) / "manifest.json"
            raw = manifest.read_bytes()
            manifest.unlink()
            row = tile(page.installed_grid, 0)
            switch = next(child for child in descendants(row) if isinstance(child, Gtk.Switch))
            switch.set_active(True)
            assert not switch.get_active()
            assert not app.extensions.all()[0]["enabled"]
            assert page.status.get_visible()
            manifest.write_bytes(raw)
            switch.set_active(True)
            assert app.extensions.all()[0]["enabled"]
            other = app.new_window()
            other.activate_action("win.extensions", None)
            assert app.extensions_window is manager
            assert len([w for w in app.get_windows() if hasattr(w, "extensions_page")]) == 1
            manager.activate_action("win.close", None)
            yield from wait(lambda: app.extensions_window is None)
            other.activate_action("win.extensions", None)
            manager = app.extensions_window
            assert manager is not None
            page = manager.extensions_page
            assert page.installed_grid.get_child_at_index(1) is not None
            assert page.restart_notice.get_visible()
            state["paintable"] = Gtk.WidgetPaintable.new(manager)
            yield from wait(lambda: manager.get_mapped())
            other.close()
            window.close()
            assert app.get_windows() == [manager]
            manager.set_default_size(820, 780)
            yield from wait(lambda: manager.get_width() == 820)
            assert page.tabs.get_width() == 600
            assert page.sections.get_width() == 600
            manager.set_default_size(420, 780)
            yield from wait(lambda: manager.get_width() == 420)
            if screenshot:
                yield from click_widget(page.search_entry, 40, 20)

                def snapshot_ready():
                    shot = Gtk.Snapshot.new()
                    state["paintable"].snapshot(shot, manager.get_width(), manager.get_height())
                    node = shot.to_node()
                    if node is None:
                        return False
                    texture = manager.get_renderer().render_texture(node, None)
                    state["texture"] = texture
                    return (texture.get_width(), texture.get_height()) == (
                        manager.get_width(),
                        manager.get_height(),
                    )

                yield from wait(snapshot_ready)
                assert state["texture"].save_to_png(str(screenshot))
                yield from market_tab(page)
                yield from wait(lambda: page.market_grid.get_mapped())
                state["paintable"] = Gtk.WidgetPaintable.new(manager)
                yield from wait(snapshot_ready)
                market_shot = screenshot.with_name(screenshot.stem + "-market.png")
                assert state["texture"].save_to_png(str(market_shot))
                page.sections.set_visible_child_name("installed")
        else:
            assert len(app.extensions.all()) == 2
            assert not page.restart_notice.get_visible()
            window.load_uri(f"http://127.0.0.1:{server.server_port}/")
            expected = "Extension active" if number == 2 else "Extension fixture"
            yield from wait(lambda: view.get_title() == expected and not view.loading)
            result = yield from command(
                view,
                "Runtime.evaluate",
                {
                    "expression": (
                        "fetch('/blocked-by-extension').then(()=>'allowed',()=>'blocked')"
                    ),
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            assert result["result"]["value"] == "blocked", "MV3 network rule did not run"
            assert Handler.blocked_requests == 0
            if number == 2:
                row = tile(page.installed_grid, 0)
                open_popup = next(
                    child
                    for child in descendants(row)
                    if isinstance(child, Gtk.Button) and child.get_label() == "Open popup"
                )
                assert open_popup.get_sensitive()
                open_popup.emit("clicked")
                uri = app.extension_launchers[content]["popup"]
                popup = app.extension_popup_windows[uri]
                yield from wait(lambda: popup.view.get_title() == "Extension popup ready")
                assert not hasattr(popup, "webview")
                assert popup.view.extension_origin == app.extension_launchers[content]["origin"]
                assert app.open_extension_popup(content) is popup
                assert popup.get_width() == 420
                if screenshot:
                    paintable = Gtk.WidgetPaintable.new(popup)

                    def popup_snapshot_ready():
                        shot = Gtk.Snapshot.new()
                        paintable.snapshot(shot, popup.get_width(), popup.get_height())
                        state["popup_node"] = shot.to_node()
                        return state["popup_node"] is not None

                    yield from wait(popup_snapshot_ready)
                    texture = popup.get_renderer().render_texture(state["popup_node"], None)
                    path = screenshot.with_name(screenshot.stem + "-popup.png")
                    assert texture.save_to_png(str(path))
                # Chromium still owns the extension origin and chrome.* APIs.
                result = yield from command(
                    popup.view,
                    "Runtime.evaluate",
                    {
                        "expression": "chrome.runtime.id",
                        "returnByValue": True,
                    },
                )
                assert result["result"]["value"] == app.extension_launchers[content]["id"]
                result = yield from command(
                    popup.view,
                    "Runtime.evaluate",
                    {
                        "expression": "navigator.clipboard.writeText('denied').then("
                        "()=>'allowed',()=>'denied')",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                assert result["result"]["value"] == "denied"
                blocked = []
                view.connect("unsupported-uri", lambda *_: blocked.append(True))
                yield from command(view, "Page.navigate", {"url": uri})
                yield from wait(lambda: bool(blocked))
                assert view.get_uri().startswith("http://127.0.0.1:")
                blocked.clear()
                popup.view.connect("unsupported-uri", lambda *_: blocked.append(True))
                other_origin = app.extension_launchers[network]["origin"]
                yield from command(
                    popup.view, "Page.navigate", {"url": other_origin + "/other.html"}
                )
                yield from wait(lambda: bool(blocked))
                assert popup.view.get_uri() == uri

                yield from click(popup, 40, 35)
                yield from wait(lambda: popup.view.get_title() == "Popup button worked")
                yield from click(popup, 40, 180)
                yield from wait(lambda: popup.view.get_title().startswith("Popup cop"))
                assert popup.view.get_title() == "Popup copied", popup.view.get_title()
                copied = []

                def clipboard_read(clipboard, result):
                    copied.append(clipboard.read_text_finish(result))

                popup.view.get_clipboard().read_text_async(None, clipboard_read)
                yield from wait(lambda: bool(copied))
                assert copied == ["Synthetic extension clipboard"]
                yield from click(popup, 40, 110)
                notification_uri = app.extension_launchers[content]["origin"] + "/notification.html"
                yield from wait(lambda: notification_uri in app.extension_popup_windows)
                notification = app.extension_popup_windows[notification_uri]
                yield from wait(lambda: notification.view.get_title() == "Extension notification")
                notification.view.command("Page.close")
                yield from wait(lambda: notification_uri not in app.extension_popup_windows)
                assert notification.view.closed and notification.view.texture is None
                popup.activate_action("win.close", None)
                assert uri not in app.extension_popup_windows
                assert popup.view.closed and popup.view.texture is None
                reopened = app.open_extension_popup(content)
                yield from wait(lambda: reopened.view.get_title() == "Extension popup ready")
                assert reopened is not popup
                reopened.close()
                assert not app.extension_popup_windows
                requests_before = len(extension_requests)
                yield from command(
                    view,
                    "Runtime.evaluate",
                    {"expression": "document.dispatchEvent(new Event('fixture-open-window'))"},
                )
                yield from wait(lambda: len(extension_requests) > requests_before)
                assert extension_requests[-1] == notification_uri
                assert not app.extension_popup_windows, "A closed extension reopened itself"
                assert all(
                    not item["url"].startswith("chrome-extension:")
                    for item in app.library.search("history")
                )
                row = tile(page.installed_grid, 0)
                assert any(
                    isinstance(child, Gtk.Label) and child.get_label() == "Content fixture"
                    for child in descendants(row)
                )
                switch = next(child for child in descendants(row) if isinstance(child, Gtk.Switch))
                switch.set_active(False)
                assert not app.extensions.all()[0]["enabled"]
                assert page.restart_notice.get_visible()
            else:
                # Keep the other extension enabled to detect Chromium reloading
                # the disabled one from its own persisted profile preferences.
                assert not app.extensions.all()[0]["enabled"]
                row = tile(page.installed_grid, 0)
                remove = next(
                    child
                    for child in descendants(row)
                    if isinstance(child, Gtk.Button)
                    and child.get_icon_name() == "user-trash-symbolic"
                )
                remove.emit("clicked")
                assert len(app.extensions.all()) == 1
                app.extensions.remove(network)
                page.refresh()
                assert page.empty.get_visible()
                assert (Path(content) / "manifest.json").is_file()
                assert (Path(network) / "manifest.json").is_file()
                market = json.loads((root / "market-install.json").read_text())
                app.extensions.register_market(market["id"], market["path"])

    steps = scenario()

    def tick():
        try:
            next(steps)
            return GLib.SOURCE_CONTINUE
        except StopIteration:
            app.quit()
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(50, tick)
    app.run(["extensions-smoke"])
    server.shutdown()
    if errors:
        raise AssertionError("\n".join(errors))
    print(f"Extension phase {number}: passed", flush=True)


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-extensions-") as temporary:
        root = Path(temporary)
        fixtures(root)
        for number in (1, 2, 3, 4):
            command = [sys.executable, __file__, "--phase", temporary, str(number)]
            if len(sys.argv) > 1 and number in (1, 2):
                command.append(sys.argv[1])
            subprocess.run(command, check=True, timeout=35)
    print(
        "Extension popups, origin guards, notification windows, MV3 APIs and restarts passed",
        flush=True,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--phase":
        phase(Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]) if len(sys.argv) > 4 else None)
    else:
        run()
