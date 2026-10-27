"""Real GTK/Chromium with a synthetic npx; never download or run DeepSeek."""

import os
import signal
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from harness_helper import fake_harness, running, runs

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Adw, GLib, Gtk


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


def loaded(window):
    return (
        window.stack.get_visible_child_name() == "web"
        and not window._deepseek_pending
        and window.webview.get_title() == "Harness fixture"
        and not window.webview.is_loading()
    )


def snapshot(window, filename):
    shot = Gtk.Snapshot.new()
    window._test_paintable.snapshot(shot, window.get_width(), window.get_height())
    texture = window.get_renderer().render_texture(shot.to_node(), None)
    assert texture.save_to_png(str(filename))


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-harness-") as temporary:
        root = Path(temporary)
        helpers = root / "helpers"
        environment = fake_harness(helpers, child=True)
        app = BrowserApplication(
            discover_devices=False,
            silent=True,
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.Harness.Run{os.getpid()}",
        )
        errors, state = [], {"phase": 0, "completed": False}
        deadline = time.monotonic() + 35

        def tick():
            try:
                assert time.monotonic() < deadline, f"Harness smoke timed out: {state['phase']}"
                phase = state["phase"]
                if phase == 0:
                    if not app.get_windows():
                        return GLib.SOURCE_CONTINUE
                    first = state["first"] = app.get_windows()[0]
                    first._test_paintable = Gtk.WidgetPaintable.new(first)
                    icon = first.deepseek_button.get_child().get_first_child()
                    assert isinstance(icon, Gtk.Image)
                    assert icon.get_paintable().get_intrinsic_width() > 0
                    assert first.recent_tiles.get_child_at_index(1) is None
                    state["phase"] = 1
                elif phase == 1:
                    first = state["first"]
                    if len(sys.argv) > 1:
                        snapshot(first, sys.argv[1])
                    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                    state["ready"] = time.monotonic() + 0.3
                    state["phase"] = 11
                elif phase == 11 and time.monotonic() >= state["ready"]:
                    first = state["first"]
                    if len(sys.argv) > 1:
                        target = Path(sys.argv[1])
                        snapshot(first, target.with_name(target.stem + "-dark.png"))
                    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
                    first.deepseek_button.emit("clicked")
                    first.deepseek_button.emit("clicked")
                    assert not first.deepseek_button.get_sensitive()
                    second = state["second"] = app.new_window()
                    second.deepseek_button.emit("clicked")
                    state["phase"] = 2
                elif phase == 2 and loaded(state["first"]) and loaded(state["second"]):
                    assert len(runs(helpers)) == 1, "DeepSeek windows must share one server"
                    assert app.library.search("history") == []
                    assert app.library.recent_apps() == []
                    assert "token=" not in state["first"].webview.get_uri()
                    state["pid"] = runs(helpers)[0]["pid"]
                    state["first"].close()
                    assert running(state["pid"])
                    state["second"].home_button.emit("clicked")
                    state["phase"] = 3
                elif phase == 3 and not running(state["pid"]):
                    # Cancel a slow startup through Home, then immediately reopen.
                    fake_harness(helpers, delay=0.4, child=True)
                    second = state["second"]
                    second.deepseek_button.emit("clicked")
                    state["phase"] = 4
                elif phase == 4 and len(runs(helpers)) >= 2:
                    second = state["second"]
                    second.home_button.emit("clicked")
                    assert second._deepseek_owner is None
                    second.deepseek_button.emit("clicked")
                    state["phase"] = 5
                elif phase == 5 and loaded(state["second"]):
                    live = [item for item in runs(helpers) if running(item["pid"])]
                    assert len(live) == 1
                    os.kill(live[0]["pid"], signal.SIGTERM)
                    state["phase"] = 6
                elif phase == 6 and state["second"].get_visible_dialog():
                    second = state["second"]
                    assert second.stack.get_visible_child_name() == "home"
                    dialog = second.get_visible_dialog()
                    assert dialog.get_heading() == "DeepSeek Harness stopped"
                    assert "private-fixture-token" not in dialog.get_body()
                    fake_harness(helpers, child=True)
                    button = next(
                        widget
                        for widget in widgets(dialog)
                        if isinstance(widget, Gtk.Button) and widget.get_label() == "Try again"
                    )
                    button.emit("clicked")
                    state["phase"] = 7
                elif phase == 7 and loaded(state["second"]):
                    assert app.library.search("history") == []
                    assert app.library.recent_apps() == []
                    state["completed"] = True
                    state["second"].close()
                    return GLib.SOURCE_REMOVE
            except Exception as error:
                errors.append(str(error))
                traceback.print_exc()
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            errors.append(f"Harness smoke timed out at phase {state['phase']}")
            app.quit()
            return GLib.SOURCE_REMOVE

        with patch.dict(os.environ, environment):
            GLib.timeout_add(100, tick)
            timer = GLib.timeout_add_seconds(40, timeout)
            try:
                app.run(["mini-browser-harness-smoke"])
            finally:
                GLib.source_remove(timer)
                app.deepseek.close()
        for item in runs(helpers):
            assert not running(item["pid"]) and not running(item["group"])
        for path in helpers.glob("child-*"):
            assert not running(int(path.name.removeprefix("child-")))
        if errors or not state["completed"]:
            raise SystemExit("; ".join(errors) or "Harness smoke exited early")
        print(
            "PASS: DeepSeek icon, shared server, token exchange, Home/close cleanup, "
            "startup cancellation, rapid reopen, crash, retry and shutdown"
        )


if __name__ == "__main__":
    run()
