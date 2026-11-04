"""Exercise MiniApps in the disposable Shell using synthetic websites and scripts."""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
from http.server import ThreadingHTTPServer
from pathlib import Path

from harness_helper import Handler, fake_harness, running, runs

# The copied extension uses this fixture launcher, never the installed browser.
if sys.argv[1:3] == ["--launcher", "list-apps"]:
    from mini_browser.launcher import main

    raise SystemExit(main(sys.argv[3:]))

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib
from mini_browser.script_apps import ScriptAppStore
from mini_browser.storage import Library

UUID = "mini-browser-pin@io.github.stone"
APP_ID = "io.github.stone.MiniBrowser.AppsSmoke.Launcher"


def browser(application_id=APP_ID):
    return BrowserApplication(application_id=application_id, discover_devices=False, silent=True)


def cold_check(identifier):
    app = browser(APP_ID + ".Cold")
    state = {"loaded": False}

    def check():
        windows = app.get_windows()
        if windows and windows[0].webview.get_title() == "Harness fixture":
            state["loaded"] = True
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(50, check)
    GLib.timeout_add_seconds(15, lambda: app.quit() or GLib.SOURCE_REMOVE)
    status = app.run(["mini-apps-cold-check", "--launch-app", identifier])
    return status or int(not state["loaded"])


def run():
    data = Path(os.environ["XDG_DATA_HOME"]) / "mini-browser"
    library = Library(data / "library.sqlite3")
    helpers = data / "synthetic-server"
    fake_harness(helpers, use_env_port=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    library.add_home_app(url, "Local Website")
    for name in ("Calendar", "Mail", "Notes"):
        library.add_home_app(f"https://{name.lower()}.example/", name)
    script = ScriptAppStore(library).save(
        "Local Demo", shlex.join([sys.executable, str(helpers / "npx")]), "", str(helpers)
    )
    try:
        cold = subprocess.run(
            [sys.executable, __file__, "--cold-check", "web:" + url],
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert cold.returncode == 0, cold.stderr
        app = browser()
        errors, state = [], {"completed": False}

        def wait(predicate):
            deadline = time.monotonic() + 12
            while not predicate():
                assert time.monotonic() < deadline, "MiniApps smoke timed out"
                yield

        def pause(seconds=0.25):
            deadline = time.monotonic() + seconds
            yield from wait(lambda: time.monotonic() >= deadline)

        def call(destination, path, interface, method, parameters):
            replies = []

            def finished(connection, result):
                try:
                    replies.append(connection.call_finish(result).unpack())
                except GLib.Error as error:
                    errors.append(str(error))

            app.get_dbus_connection().call(
                destination,
                path,
                interface,
                method,
                parameters,
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
                finished,
            )
            yield from wait(lambda: bool(replies) or bool(errors))
            assert not errors, errors
            return replies[0]

        def extension(method):
            return (
                yield from call(
                    "org.gnome.Shell",
                    "/org/gnome/Shell",
                    "org.gnome.Shell.Extensions",
                    method,
                    GLib.Variant("(s)", (UUID,)),
                )
            )

        def menu(action="state", value=""):
            reply = yield from call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "MiniApps",
                GLib.Variant("(oss)", (state["home"].pin.path, action, value)),
            )
            return json.loads(reply[0])

        def opened(category="All Apps"):
            yield from menu("open")
            deadline = time.monotonic() + 8
            while True:
                result = yield from menu()
                if result["apps"]:
                    break
                assert time.monotonic() < deadline, result
                yield
            yield from pause()
            yield from menu("click", category)
            yield from pause()
            return (yield from menu())

        def scenario():
            yield from wait(lambda: bool(app.get_windows()))
            home = state["home"] = app.get_windows()[0]
            yield from wait(lambda: home.get_mapped() and bool(home.pin.path))
            # Enabling the existing pin extension also starts MiniApps automatically.
            assert (yield from menu())["exists"]
            result = yield from opened()
            expected = {"web:" + record["url"] for record in app.library.web_apps()}
            expected.update(("builtin:deepseek", "script:" + script["id"]))
            assert result["exists"] and result["open"], result
            assert set(result["apps"]) == expected, result
            assert result["selected"] == ["All Apps"], result
            assert 450 <= result["width"] <= 650, result
            if len(sys.argv) > 1:
                yield from pause(1)
                yield from menu("snapshot", str(Path(sys.argv[1]).resolve()))
            yield from menu("click", "Websites")
            yield from pause()
            result = yield from menu()
            assert result["selected"] == ["Websites"], result
            assert all(identifier.startswith("web:") for identifier in result["apps"])
            yield from menu("click", "web:" + url)
            yield from wait(lambda: len(app.get_windows()) == 2)
            web = next(window for window in app.get_windows() if window is not home)
            yield from wait(lambda: web.webview.get_title() == "Harness fixture")
            assert not (yield from menu())["open"]
            web.close()
            yield from opened("Scripts")
            result = yield from menu()
            assert set(result["apps"]) == {"builtin:deepseek", "script:" + script["id"]}, result
            # Native keyboard activation follows the same managed-server lifecycle as Home.
            yield from menu("focus", "script:" + script["id"])
            yield from menu("key", "Return")
            yield from wait(lambda: len(app.get_windows()) == 2)
            script_window = next(window for window in app.get_windows() if window is not home)
            yield from wait(lambda: script_window.webview.get_title() == "Harness fixture")
            assert len(runs(helpers)) == 1
            script_window.close()
            yield from wait(lambda: not running(runs(helpers)[0]["pid"]))
            try:
                app.launch_app("script:missing")
            except ValueError:
                pass
            else:
                raise AssertionError("Removed app ID was accepted")
            app.library.uninstall_web_app(url)
            result = yield from opened()
            assert "web:" + url not in result["apps"], result
            yield from menu("key", "Escape")
            yield from pause()
            assert not (yield from menu())["open"]
            # Long categories retain every app in a bounded, scrollable menu.
            for index in range(35):
                app.script_apps.save(f"Demo {index:02}", f"python3 demo-{index}.py", "", str(data))
            result = yield from opened("Scripts")
            assert len(result["apps"]) == 37, result
            # A failed read is actionable and the next menu opening retries it.
            yield from menu("close")
            backup = data / "library.backup"
            app.library.path.replace(backup)
            app.library.path.write_bytes(b"synthetic invalid SQLite")
            try:
                yield from menu("open")
                deadline = time.monotonic() + 8
                while True:
                    result = yield from menu()
                    if any("Could not load apps" in label for label in result["labels"]):
                        break
                    assert time.monotonic() < deadline, result
                    yield
            finally:
                app.library.path.unlink()
                backup.replace(app.library.path)
            yield from menu("close")
            yield from opened()
            # Disable during an asynchronous refresh, then re-enable without duplicate buttons.
            yield from menu("close")
            yield from menu("open")
            yield from extension("DisableExtension")
            assert not (yield from menu())["exists"]
            yield from extension("EnableExtension")
            assert (yield from opened())["exists"]
            yield from menu("close")
            state["completed"] = True
            print(
                "PASS: MiniApps panel menu, categories, icons, cold/warm launch, keyboard, "
                "script cleanup, removals, scrolling, read failure/retry and enable/disable",
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

        GLib.timeout_add(40, tick)
        app.run(["mini-apps-smoke"])
        assert state["completed"] and not errors, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--launcher"]:
        raise SystemExit(browser().run(["mini-apps-launcher", *sys.argv[2:]]))
    if sys.argv[1:2] == ["--cold-check"]:
        raise SystemExit(cold_check(sys.argv[2]))
    run()
