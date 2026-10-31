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
        and not window._script_pending
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
        environment = fake_harness(
            helpers,
            child=True,
            delay=2,
            messages=["Installing synthetic package", "token=private-fixture-token"],
        )
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
                    first = state["home"] = app.get_windows()[0]
                    first._test_paintable = Gtk.WidgetPaintable.new(first)
                    icon = first.deepseek_button.get_child().get_first_child()
                    assert isinstance(icon, Gtk.Image)
                    assert icon.get_paintable().get_intrinsic_width() > 0
                    assert (
                        first.app_tiles.get_child_at_index(0).get_child() == first.deepseek_button
                    )
                    assert first.app_tiles.get_child_at_index(2) is not None
                    assert first.app_tiles.get_child_at_index(3) is None
                    state["phase"] = 1
                elif phase == 1:
                    first = state["home"]
                    if len(sys.argv) > 1:
                        snapshot(first, sys.argv[1])
                    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                    state["ready"] = time.monotonic() + 0.3
                    state["phase"] = 11
                elif phase == 11 and time.monotonic() >= state["ready"]:
                    first = state["home"]
                    if len(sys.argv) > 1:
                        target = Path(sys.argv[1])
                        snapshot(first, target.with_name(target.stem + "-dark.png"))
                    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
                    before = set(app.get_windows())
                    first.deepseek_button.emit("clicked")
                    launched = state["first"] = (set(app.get_windows()) - before).pop()
                    assert launched.stack.get_visible_child_name() == "script-loading"
                    assert launched._script_pending
                    loading = launched.script_loading
                    assert loading.output.get_text() == "▌"
                    assert loading.output.has_css_class("monospace")
                    assert not any(
                        isinstance(widget, (Gtk.Spinner, Gtk.Frame)) for widget in widgets(loading)
                    )
                    assert [
                        widget for widget in widgets(loading) if isinstance(widget, Gtk.Label)
                    ] == [loading.output]
                    assert launched.get_title() == "DeepSeek Harness"
                    assert first.stack.get_visible_child_name() == "home"
                    launched._test_paintable = Gtk.WidgetPaintable.new(launched)
                    before = set(app.get_windows())
                    first.deepseek_button.emit("clicked")
                    state["second"] = (set(app.get_windows()) - before).pop()
                    state["phase"] = 12
                elif phase == 12:
                    launched = state["first"]
                    loading = launched.script_loading
                    output = loading.output.get_text()
                    assert "private-fixture-token" not in output
                    if 1 < len(output) < len(loading._target):
                        state["typed"] = True
                        assert output.endswith("▌")
                    if "[redacted]" not in output:
                        return GLib.SOURCE_CONTINUE
                    assert state.get("typed"), "Startup output must appear incrementally"
                    assert "Installing synthetic package" in output
                    assert "[redacted]" in output
                    assert output[-1] in {"▌", "\u00a0"}
                    valid, bounds = loading.scroll.compute_bounds(loading)
                    assert valid
                    assert (
                        abs(bounds.get_y() + bounds.get_height() / 2 - loading.get_height() / 2) < 2
                    )
                    valid, bounds = loading.output.compute_bounds(loading)
                    assert valid
                    assert (
                        abs(bounds.get_x() + bounds.get_width() / 2 - loading.get_width() / 2) < 2
                    )
                    # Leave the completed log idle long enough for its cursor to blink.
                    state["cursor"] = output[-1]
                    state["ready"] = time.monotonic() + 0.6
                    state["phase"] = 13
                elif phase == 13 and time.monotonic() >= state["ready"]:
                    output = state["first"].script_loading.output.get_text()
                    assert output[-1] != state["cursor"], "The end cursor must blink while idle"
                    if len(sys.argv) > 1:
                        target = Path(sys.argv[1])
                        snapshot(state["first"], target.with_name(target.stem + "-loading.png"))
                    state["phase"] = 2
                elif phase == 2 and loaded(state["first"]) and loaded(state["second"]):
                    for window in (state["first"], state["second"]):
                        assert window.script_loading._animation_source == 0
                        assert window.script_loading.output.get_text() == ""
                        assert window._script_log_source == 0
                    assert len(runs(helpers)) == 1, "DeepSeek windows must share one server"
                    assert app.library.search("history") == []
                    assert app.library.recent_apps() == []
                    assert "token=" not in state["first"].webview.get_uri()
                    state["pid"] = runs(helpers)[0]["pid"]
                    state["second"].set_default_size(850, 650)
                    state["first"].close()
                    state["phase"] = 21
                elif phase == 21:
                    second = state["second"]
                    if (second.get_width(), second.get_height()) != (850, 650):
                        return GLib.SOURCE_CONTINUE
                    assert running(state["pid"])
                    second.home_button.emit("clicked")
                    state["phase"] = 3
                elif phase == 3 and not running(state["pid"]):
                    assert state["second"]._closing and app.get_windows() == [state["home"]]
                    # Cancel a slow startup through Home, then immediately reopen.
                    fake_harness(helpers, delay=0.4, child=True)
                    state["second"] = state["home"]._open_deepseek()
                    state["phase"] = 4
                elif phase == 4 and len(runs(helpers)) >= 2:
                    second = state["second"]
                    second.home_button.emit("clicked")
                    state["phase"] = "cancel-startup"
                elif phase == "cancel-startup":
                    second = state["second"]
                    if not second._closing:
                        return GLib.SOURCE_CONTINUE
                    assert second._script_owner is None
                    assert second._closing and app.get_windows() == [state["home"]]
                    state["second"] = state["home"]._open_deepseek()
                    state["phase"] = 5
                elif phase == 5 and loaded(state["second"]):
                    assert state["second"].get_default_size() == (850, 650)
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
                    assert state["second"].get_default_size() == (850, 650)
                    assert app.library.search("history") == []
                    assert app.library.recent_apps() == []
                    state["second"].close()
                    fake_harness(helpers, delay=2, child=True, messages=["Starting fixture"])
                    state["previous_runs"] = len(runs(helpers))
                    before = set(app.get_windows())
                    state["home"].deepseek_button.emit("clicked")
                    state["cancelled"] = (set(app.get_windows()) - before).pop()
                    state["phase"] = 8
                elif phase == 8 and len(runs(helpers)) > state["previous_runs"]:
                    cancelled = state["cancelled"]
                    assert cancelled._script_pending
                    cancelled.close()
                    assert cancelled._script_owner is None
                    assert cancelled._script_log_source == 0
                    assert cancelled.script_loading.output.get_text() == ""
                    assert cancelled.script_loading._animation_source == 0
                    state["phase"] = 9
                elif phase == 9 and not any(running(item["pid"]) for item in runs(helpers)):
                    state["completed"] = True
                    state["home"].close()
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
            "PASS: DeepSeek launch window, centered monospace log, typing and cursor, "
            "redaction, icon, shared server, token exchange, Home/close cleanup, "
            "startup cancellation, rapid reopen, crash, retry and shutdown"
        )


if __name__ == "__main__":
    run()
