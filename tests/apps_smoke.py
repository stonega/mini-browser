"""Add website/script tiles with native forms and launch only a synthetic local server."""

import os
import shlex
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path

from harness_helper import fake_harness, running, runs

from mini_browser.app_dialogs import AddAppDialog
from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gdk, Gio, GLib, Gtk
from mini_browser.script_apps import ScriptAppStore
from mini_browser.storage import Library


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-apps-") as temporary:
        root = Path(temporary)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        helpers = root / "helpers"
        fake_harness(helpers, use_env_port=True, delay=0.3, child=True)
        url = f"http://127.0.0.1:{port}/"
        app = BrowserApplication(
            discover_devices=False,
            silent=True,
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.AppsSmoke.Run{os.getpid()}",
        )
        errors, state = [], {"completed": False}

        def wait(predicate):
            deadline = time.monotonic() + 8
            while not predicate():
                assert time.monotonic() < deadline, "Add app smoke timed out"
                yield

        def snapshot(window, suffix):
            if len(sys.argv) > 1:
                shot = Gtk.Snapshot.new()
                state["paintable"].snapshot(shot, window.get_width(), window.get_height())
                texture = window.get_renderer().render_texture(shot.to_node(), None)
                path = Path(sys.argv[1])
                assert texture.save_to_png(str(path.with_name(path.stem + suffix + ".png")))

        def add_dialog(home):
            valid, bounds = home.add_app_button.compute_bounds(home)
            assert valid
            completed = []

            def finished(connection, result):
                try:
                    connection.call_finish(result)
                    completed.append(True)
                except GLib.Error as error:
                    errors.append(str(error))

            app.get_dbus_connection().call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "Click",
                GLib.Variant(
                    "(oddu)",
                    (
                        home.pin.path,
                        bounds.get_x() + bounds.get_width() / 2,
                        bounds.get_y() + bounds.get_height() / 2,
                        1,
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                finished,
            )
            yield from wait(lambda: bool(completed))
            yield from wait(lambda: home.get_visible_dialog() is not None)
            dialog = home.get_visible_dialog()
            assert isinstance(dialog, AddAppDialog)
            return dialog

        def scenario():
            yield from wait(lambda: bool(app.get_windows()))
            home = app.get_windows()[0]
            state["paintable"] = Gtk.WidgetPaintable.new(home)
            yield from wait(lambda: home.add_app_button.get_allocated_width() >= 48)
            for section in ("history", "agent", "apps"):
                home.home_sections.set_visible_child_name(section)
                assert home.add_app_button.get_visible() == (section == "apps")
            yield
            valid, search = home.home_entry.compute_bounds(home.home_search)
            assert valid
            valid, add = home.add_app_button.compute_bounds(home.home_search)
            assert valid and search.get_x() + search.get_width() < add.get_x()
            assert home.home_search.get_width() <= 600
            other = app.new_window()
            yield from wait(lambda: other.get_mapped())
            dialog = yield from add_dialog(home)
            assert not dialog.script_group.get_visible()
            dialog.submit.emit("clicked")
            assert dialog.status.get_visible()
            assert app.library.home_apps() == []
            dialog.name.set_text("Fixture website")
            dialog.url.set_text("https://example.org/dashboard")
            dialog.submit.emit("clicked")
            yield from wait(lambda: home.get_visible_dialog() is None)
            assert len(app.library.home_apps()) == 1
            assert len(Library(app.library.path).home_apps()) == 1
            for window in (home, other):
                assert window.app_tiles.get_child_at_index(3) is not None
                assert window.app_tiles.get_child_at_index(4) is None
            assert app.library.search("history") == []
            assert app.library.search("bookmarks") == []
            dialog = yield from add_dialog(home)
            dialog.kind.set_selected(1)
            assert dialog.script_group.get_visible()
            assert not dialog.url.get_visible()
            dialog.name.set_text("Fixture script")
            dialog.command.set_text(shlex.join([str(helpers / "npx"), "two words"]))
            dialog.directory.set_text(str(root))
            dialog.port.set_text("70000")
            dialog.submit.emit("clicked")
            assert dialog.status.get_visible()
            assert app.script_apps.all() == []
            dialog.port.set_text("")
            dialog.status.set_visible(False)
            ready = time.monotonic() + 0.25
            yield from wait(lambda: time.monotonic() >= ready)
            snapshot(home, "-script-form")
            dialog.submit.emit("clicked")
            yield from wait(lambda: home.get_visible_dialog() is None)
            saved = app.script_apps.all()
            assert len(saved) == 1
            assert saved[0]["port"] == 0
            assert ScriptAppStore(Library(app.library.path)).all() == saved
            assert runs(helpers) == []
            ready = time.monotonic() + 0.25
            yield from wait(lambda: time.monotonic() >= ready)
            snapshot(home, "-home")
            # Dismissing an Add form never saves an app.
            dialog = yield from add_dialog(home)
            dialog.force_close()
            assert len(app.library.home_apps()) == len(app.script_apps.all()) == 1
            first = home._open_script_app(saved[0])
            assert first.stack.get_visible_child_name() == "script-loading"
            assert first._script_pending
            yield from wait(lambda: first.webview.get_title() == "Harness fixture")
            automatic_url = first.webview.get_uri()
            assert automatic_url == f"http://127.0.0.1:{runs(helpers)[0]['port_env']}/"
            assert not first.lookup_action("bookmark").get_enabled()
            assert first.window_size.key[0] == "script"
            second = home._open_script_app(saved[0])
            yield from wait(lambda: second.webview.get_title() == "Harness fixture")
            assert second.webview.get_uri() == automatic_url
            assert len(runs(helpers)) == 1
            assert app.library.search("history") == []
            first.activate_action("win.home", None)
            yield from wait(lambda: first not in app.get_windows())
            assert running(runs(helpers)[0]["pid"])
            # Uninstall removes the script tile across Home windows, preserving open instances.
            tile = home.app_tiles.get_child_at_index(1).get_child()
            keys = next(
                c for c in tile.observe_controllers() if isinstance(c, Gtk.EventControllerKey)
            )
            assert keys.emit("key-pressed", Gdk.KEY_Menu, 0, Gdk.ModifierType(0))
            home._app_menu.activate_action("app.uninstall", None)
            assert app.script_apps.all() == []
            for window in (home, other):
                assert window.app_tiles.get_child_at_index(3) is not None
                assert window.app_tiles.get_child_at_index(4) is None
            assert running(runs(helpers)[0]["pid"])
            second.close()
            yield from wait(lambda: all(not running(item["pid"]) for item in runs(helpers)))
            # A custom port is saved by the native form and passed to the command unchanged.
            dialog = yield from add_dialog(home)
            dialog.kind.set_selected(1)
            dialog.name.set_text("Custom port")
            dialog.command.set_text(saved[0]["command"])
            dialog.directory.set_text(str(root))
            dialog.port.set_text(str(port))
            dialog.submit.emit("clicked")
            yield from wait(lambda: home.get_visible_dialog() is None)
            custom = app.script_apps.all()[0]
            assert custom["port"] == port
            custom_window = home._open_script_app(custom)
            yield from wait(lambda: custom_window.webview.get_title() == "Harness fixture")
            assert custom_window.webview.get_uri() == url
            assert runs(helpers)[-1]["port_env"] == str(port)
            custom_window.close()
            yield from wait(lambda: all(not running(item["pid"]) for item in runs(helpers)))
            # Closing during startup cancels the new process as well.
            fake_harness(helpers, use_env_port=True, delay=1, child=True)
            record = app.script_apps.save("Cancelled", saved[0]["command"], port, str(root))
            previous_runs = len(runs(helpers))
            pending = home._open_script_app(record)
            yield from wait(lambda: len(runs(helpers)) == previous_runs + 1)
            pending.close()
            yield from wait(lambda: all(not running(item["pid"]) for item in runs(helpers)))
            for child in helpers.glob("child-*"):
                yield from wait(
                    lambda child=child: not running(int(child.name.removeprefix("child-")))
                )
            other.close()
            state["completed"] = True
            print(
                "PASS: Apps-only Add button, native web/script forms, validation, persistence, "
                "automatic/custom PORT and managed URL, multi-window refresh, shared local server, "
                "script sizing, Home/close cleanup, "
                "uninstall and startup cancellation",
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
                errors.append(str(error) or type(error).__name__)
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            errors.append("Add app smoke timed out")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, tick)
        timeout_source = GLib.timeout_add_seconds(40, timeout)
        try:
            app.run(["mini-browser-apps-smoke"])
        finally:
            GLib.source_remove(timeout_source)
        if errors or not state["completed"]:
            raise SystemExit("; ".join(errors) or "Add app smoke exited early")


if __name__ == "__main__":
    run()
