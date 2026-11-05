"""Real Chromium icon loading and native web-app editing, using loopback fixtures."""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from icon_helper import png
from smoke import widgets

from mini_browser.app_dialogs import EditWebAppDialog
from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gdk, GdkPixbuf, GLib, Gtk
from mini_browser.launcher import app_catalog
from mini_browser.storage import Library


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/manifest")
            self.end_headers()
            return
        if path == "/assets/app.webmanifest":
            mime, body = (
                "application/manifest+json",
                json.dumps(
                    {
                        "icons": [
                            {"src": "/not-image", "sizes": "512x512"},
                            {"src": "app.svg", "sizes": "256x256", "purpose": "any"},
                        ]
                    }
                ).encode(),
            )
        elif path == "/assets/app.svg":
            mime, body = (
                "image/svg+xml",
                (
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
                    b'<rect width="256" height="256" fill="#2850c8"/></svg>'
                ),
            )
        elif path in ("/favicon.ico", "/favicon.png"):
            mime, body = (
                "image/png",
                png((220, 120, 40) if path.endswith("ico") else (40, 180, 100)),
            )
        elif path == "/not-image":
            mime, body = "text/plain", b"not an image"
        elif path == "/early-icon.png":
            mime, body = "image/png", png((100, 60, 180))
        elif path == "/pending-resource":
            self.server.release_page.wait(8)
            mime, body = "image/png", png()
        elif path == "/slow.png":
            self.server.icon_started.set()
            self.server.release_icon.wait(8)
            mime, body = "image/png", png((180, 40, 40))
        else:
            links = {
                "/manifest": (
                    '<link rel="manifest" href="/assets/app.webmanifest">'
                    '<link rel="icon" href="/favicon.png">'
                ),
                "/favicon": '<link rel="icon" href="/favicon.png">',
                "/broken": '<link rel="icon" href="/not-image">',
                "/slow": '<link rel="icon" href="/slow.png">',
                "/pending": (
                    '<link rel="icon" href="/early-icon.png"><img src="/pending-resource">'
                ),
            }.get(path, "")
            mime, body = (
                "text/html",
                f"<title>Icon fixture {path}</title>{links}<h1>App fixture</h1>".encode(),
            )
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-icons-") as temporary:
        root = Path(temporary)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.icon_started, server.release_icon = threading.Event(), threading.Event()
        server.release_page = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            discover_devices=False,
            silent=True,
            application_id=f"io.github.stone.MiniBrowser.AppsSmoke.Icons{os.getpid()}",
        )
        errors, state = [], {"completed": False}

        def wait(predicate):
            deadline = time.monotonic() + 15
            while not predicate():
                assert time.monotonic() < deadline, "Website icon smoke timed out"
                yield

        def color(path):
            return tuple(GdkPixbuf.Pixbuf.new_from_file(str(path)).get_pixels()[:3])

        def shot(window, suffix):
            if len(sys.argv) > 1:
                paintable = Gtk.WidgetPaintable.new(window)
                snapshot = Gtk.Snapshot.new()
                paintable.snapshot(snapshot, window.get_width(), window.get_height())
                texture = window.get_renderer().render_texture(snapshot.to_node(), None)
                path = Path(sys.argv[1])
                assert texture.save_to_png(str(path.with_name(path.stem + suffix + ".png")))

        def scenario():
            yield from wait(lambda: bool(app.get_windows()))
            home = app.get_windows()[0]
            saved = app.library.add_home_app(origin + "/manifest", "Fixture app")
            home.refresh_recents()
            page = app.new_window(saved["url"])
            yield from wait(lambda: app.site_icons.path_for(origin) is not None)
            assert color(app.site_icons.path_for(origin)) == (40, 80, 200), "Manifest icon must win"
            record = next(
                record
                for record in app_catalog(app.library, app.site_icons)
                if record["id"] == "web:" + saved["url"]
            )
            assert record["icon"] == str(app.site_icons.path_for(origin))
            tile = next(
                widget
                for widget in widgets(home.app_tiles)
                if isinstance(widget, Gtk.Button)
                and widget.get_tooltip_text() == f"Fixture app\n{saved['url']}"
            )
            assert any(isinstance(widget, Gtk.Image) for widget in widgets(tile))
            keys = next(
                controller
                for controller in tile.observe_controllers()
                if isinstance(controller, Gtk.EventControllerKey)
            )
            assert keys.emit("key-pressed", Gdk.KEY_Menu, 0, Gdk.ModifierType(0))
            model = home._app_menu.get_menu_model()
            assert [
                model.get_item_attribute_value(i, "label", None).unpack() for i in range(2)
            ] == ["Edit", "Uninstall"]
            home._app_menu.activate_action("app.edit", None)
            yield from wait(lambda: home.get_visible_dialog() is not None)
            dialog = home.get_visible_dialog()
            assert isinstance(dialog, EditWebAppDialog)
            dialog.name.set_text("Dashboard")
            dialog.path.set_text("https://other.example/")
            dialog.submit.emit("clicked")
            assert dialog.status.get_visible()
            assert app.library.home_apps()[0]["url"] == saved["url"]
            dialog.path.set_text("/dashboard?view=home#top")
            custom = root / "custom.png"
            custom.write_bytes(png((150, 40, 180), 64))
            dialog.set_icon_file(custom)
            yield from wait(lambda: dialog.get_mapped())
            ready = time.monotonic() + 0.5
            yield from wait(lambda: time.monotonic() >= ready)
            shot(home, "-edit")
            dialog.submit.emit("clicked")
            yield from wait(lambda: home.get_visible_dialog() is None)
            custom.unlink()
            updated = app.library.home_apps()[0]
            assert updated["url"] == origin + "/dashboard?view=home#top"
            assert Library(app.library.path).home_apps() == app.library.home_apps()
            record = next(
                record
                for record in app_catalog(app.library, app.site_icons)
                if record["title"] == "Dashboard"
            )
            assert color(record["icon"]) == (150, 40, 180)
            launched = app.launch_app(record["id"])
            yield from wait(lambda: launched.webview.get_title() == "Icon fixture /dashboard")
            assert launched.webview.get_uri() == updated["url"]
            launched.close()
            page.load_uri(origin + "/favicon")
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (40, 180, 100))
            # Resetting the custom image restores the shared website icon.
            updated = next(
                record for record in app.library.web_apps() if record["title"] == "Dashboard"
            )
            home._edit_web_app(updated)
            yield from wait(lambda: home.get_visible_dialog() is not None)
            dialog = home.get_visible_dialog()
            dialog.reset_icon.emit("clicked")
            dialog.submit.emit("clicked")
            yield from wait(lambda: home.get_visible_dialog() is None)
            record = next(
                record
                for record in app_catalog(app.library, app.site_icons)
                if record["title"] == "Dashboard"
            )
            assert color(record["icon"]) == (40, 180, 100)
            # Invalid declared images fall through to the origin's default favicon.
            page.load_uri(origin + "/broken")
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (220, 120, 40))
            page.load_uri(origin + "/slow")
            yield from wait(server.icon_started.is_set)
            page.load_uri(origin + "/favicon")
            server.release_icon.set()
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (40, 180, 100))
            ready = time.monotonic() + 0.3
            yield from wait(lambda: time.monotonic() >= ready)
            assert color(app.site_icons.path_for(origin)) == (40, 180, 100)
            # A saved launcher must learn the icon even when its hostname redirects.
            redirect_origin = f"http://localhost:{server.server_port}"
            redirected = app.library.add_home_app(redirect_origin + "/redirect", "Redirect app")
            redirected_page = app.new_window(redirected["url"])
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (40, 80, 200))
            assert app.site_icons.path_for(redirect_origin), "Redirected launcher lost its icon"
            assert color(app.site_icons.path_for(redirect_origin)) == (40, 80, 200)
            record = next(
                record
                for record in app_catalog(app.library, app.site_icons)
                if record["id"] == "web:" + redirected["url"]
            )
            assert record["icon"] == str(app.site_icons.path_for(redirect_origin))
            # Later page-driven navigations must not replace the launch address's icon.
            redirected_page.webview.evaluate_javascript(
                "location.href = '/favicon'", -1, None, None, None, None
            )
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (40, 180, 100))
            assert color(app.site_icons.path_for(redirect_origin)) == (40, 80, 200)
            redirected_page.close()
            page.load_uri(origin + "/pending")
            yield from wait(lambda: color(app.site_icons.path_for(origin)) == (100, 60, 180))
            assert page.webview.loading, "Website icons must not wait for slow page resources"
            page.webview.evaluate_javascript(
                "location.href = " + json.dumps(redirect_origin + "/favicon"),
                -1,
                None,
                None,
                None,
                None,
            )
            yield from wait(
                lambda: color(app.site_icons.path_for(redirect_origin)) == (40, 180, 100)
            )
            assert color(app.site_icons.path_for(origin)) == (100, 60, 180)
            server.release_page.set()
            yield from wait(lambda: not page.webview.loading)
            page.close()
            shot(home, "-home")
            state["completed"] = True
            print(
                "PASS: manifest SVG, favicon and default fallback, shared cached PNGs, "
                "stale navigation, redirected launcher icons, icons before full page load, "
                "Edit before Uninstall, "
                "title/path/custom icon edits, "
                "reset, persistence and launch URL",
                flush=True,
            )
            app.quit()

        steps = scenario()

        def tick():
            try:
                next(steps)
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception as error:
                traceback.print_exc()
                errors.append(str(error))
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(40, tick)
        try:
            app.run(["site-icons-smoke"])
            assert state["completed"] and not errors, errors
        finally:
            server.release_icon.set()
            server.release_page.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    run()
