"""Real GTK/H.264/RTP with synthetic helpers; never enumerate or connect real phones."""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from test_devices import fake_helpers

from mini_browser.application import BrowserApplication
from mini_browser.device_dialogs import DeviceEditor, DevicesDialog
from mini_browser.gtk import Gdk, GLib, Gtk


def fixture_video(root):
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    pipeline = Gst.parse_launch(
        "videotestsrc num-buffers=30 pattern=ball "
        "! video/x-raw,width=240,height=480,framerate=15/1 "
        "! openh264enc bitrate=200000 gop-size=15 ! h264parse config-interval=-1 "
        f'! video/x-h264,stream-format=byte-stream ! filesink location="{root}/fixture.h264"'
    )
    pipeline.set_state(Gst.State.PLAYING)
    message = pipeline.get_bus().timed_pop_filtered(
        10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS
    )
    pipeline.set_state(Gst.State.NULL)
    assert message and message.type == Gst.MessageType.EOS, "Could not create synthetic H.264"


def screenshot(window, suffix):
    if len(sys.argv) < 2:
        return
    target = Path(sys.argv[1])
    target = target.with_name(target.stem + suffix + ".png")
    paintable = window._test_paintable
    shot = Gtk.Snapshot.new()
    paintable.snapshot(shot, window.get_width(), window.get_height())
    node = shot.to_node()
    texture = window.get_renderer().render_texture(node, None)
    assert texture.save_to_png(str(target))


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-phones-") as temp:
        root = Path(temp)
        fixture_video(root)
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.Phones.Run{os.getpid()}",
        )
        errors, state = [], {"phase": 0, "completed": False}
        deadline = time.monotonic() + 70

        def tick():
            try:
                assert time.monotonic() < deadline, (
                    f"Phone smoke timed out at phase {state['phase']}: "
                    f"{[(w.state, w.frame_size) for w in app.device_windows.values()]}"
                )
                phase = state["phase"]
                if phase == 0:
                    windows = app.get_windows()
                    if not windows:
                        return GLib.SOURCE_CONTINUE
                    home = state["home"] = windows[0]
                    if not app.connected_android:
                        return GLib.SOURCE_CONTINUE
                    assert app.devices.all() == []
                    detected, status = app.device_tiles()[0]
                    assert status == "Connected"
                    assert home.app_tiles.get_child_at_index(1).get_child() == home.deepseek_button
                    assert home.app_tiles.get_child_at_index(3) is not None
                    assert home.app_tiles.get_child_at_index(4) is None
                    home.app_tiles.get_child_at_index(0).get_child().emit("clicked")
                    state["detected_window"] = app.device_windows[detected.id]
                    state["detected_window"]._test_paintable = Gtk.WidgetPaintable.new(
                        state["detected_window"]
                    )
                    manager = state["manager"] = DevicesDialog(home)
                    editor = state["editor"] = DeviceEditor(manager, "android")
                    editor.find_phones()
                    state["phase"] = 1
                elif phase == 1:
                    editor = state["editor"]
                    if not getattr(editor, "choice_rows", []):
                        return GLib.SOURCE_CONTINUE
                    editor.choice_rows[0].emit("activated")
                    assert editor.address.get_text() == "synthetic-phone"
                    editor.save_button.emit("clicked")
                    editor = DeviceEditor(state["manager"], "iphone")
                    editor.name.set_text("Synthetic iPhone")
                    editor.save_button.emit("clicked")
                    android, iphone = app.devices.all()
                    state.update(android=android, iphone=iphone)
                    assert len(state["manager"].rows) == 2
                    assert app.library.recent_apps() == []
                    rename = DeviceEditor(state["manager"], android.kind, android)
                    rename.name.set_text("Synthetic Android")
                    rename.save_button.emit("clicked")
                    assert app.devices.all()[0].id == android.id
                    state["manager"].force_close()
                    tile = state["home"].app_tiles.get_child_at_index(0).get_child()
                    tile.emit("clicked")
                    window = state["android_window"] = app.device_windows[android.id]
                    assert window is state["detected_window"]
                    assert app.open_device(android.id) is window
                    assert len(app.device_windows) == 1
                    state["phase"] = 2
                elif phase == 2:
                    window = state["android_window"]
                    assert window.state != "error", window.status_page.get_description()
                    if window.state != "streaming":
                        return GLib.SOURCE_CONTINUE
                    assert window.frame_size == (240, 480), window.frame_size
                    assert window.native_size == (1080, 2160)
                    assert window.picture.get_paintable() is not None
                    window.set_default_size(550, 740)
                    state["first_frame"] = window.video.last_frame
                    state["phase"] = 3
                elif phase == 3:
                    window = state["android_window"]
                    if window.video.last_frame <= state["first_frame"] + 0.2:
                        return GLib.SOURCE_CONTINUE
                    screenshot(window, "-android")
                    # Synthetic callbacks lack a real Wayland activation token. Exercise
                    # the focus guard explicitly while retaining real transport/input I/O.
                    with patch.object(window, "is_active", return_value=False):
                        with patch.object(window.transport, "send") as send:
                            window.key("3")
                            send.assert_not_called()
                    with patch.object(window, "is_active", return_value=True):
                        window._pressed(
                            None, window.picture.get_width() / 2, window.picture.get_height() / 2
                        )
                        window._released(None, 0, 0)
                        window._key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType(0))
                        window.key("3")
                    state["phase"] = 4
                elif phase == 4:
                    inputs = root / "inputs.jsonl"
                    lines = inputs.read_text().splitlines() if inputs.exists() else []
                    if len(lines) < 3:
                        return GLib.SOURCE_CONTINUE
                    assert [json.loads(line) for line in lines] == [
                        ["tap", "540", "1080"],
                        ["text", "a"],
                        ["keyevent", "3"],
                    ], lines
                    state["android_window"].disconnect()
                    assert state["android_window"].picture.get_paintable() is None
                    state["phase"] = 5
                elif phase == 5:
                    if app.device_transports:
                        return GLib.SOURCE_CONTINUE
                    window = state["android_window"]
                    assert window.get_default_size() == (550, 740)
                    window.home_button.emit("clicked")
                    state["phase"] = "home"
                elif phase == "home":
                    window = state["android_window"]
                    if not window._closing:
                        return GLib.SOURCE_CONTINUE
                    assert window._closing and window not in app.get_windows()
                    assert app.get_windows() == [state["home"]]
                    window = state["android_window"] = app.open_device(state["android"].id)
                    window._test_paintable = Gtk.WidgetPaintable.new(window)
                    assert window.get_default_size() == (550, 740)
                    state["phase"] = 6
                elif phase == 6:
                    window = state["android_window"]
                    assert window.state != "error", window.status_page.get_description()
                    if window.state != "streaming":
                        return GLib.SOURCE_CONTINUE
                    app.disconnect_devices()
                    assert window.state == "disconnected"
                    assert window.picture.get_paintable() is None
                    state["iphone_window"] = app.open_device(state["iphone"].id)
                    assert state["iphone_window"].get_default_size() == (420, 780)
                    state["iphone_window"]._test_paintable = Gtk.WidgetPaintable.new(
                        state["iphone_window"]
                    )
                    state["phase"] = 7
                elif phase == 7:
                    window = state["iphone_window"]
                    assert window.state != "error", window.status_page.get_description()
                    if window.state != "streaming":
                        return GLib.SOURCE_CONTINUE
                    if "iphone_ready_at" not in state:
                        state["iphone_ready_at"] = time.monotonic()
                    if time.monotonic() - state["iphone_ready_at"] < 0.2:
                        return GLib.SOURCE_CONTINUE
                    assert window.frame_size == (240, 480)
                    assert not window.lookup_action("phone-home").get_enabled()
                    assert window.pairing_code.get_text() == ""
                    screenshot(window, "-iphone")
                    (root / "iphone-paused").touch()
                    state["phase"] = 8
                elif phase == 8:
                    window = state["iphone_window"]
                    if window.state != "waiting":
                        return GLib.SOURCE_CONTINUE
                    assert window.picture.get_paintable() is None
                    (root / "iphone-paused").unlink()
                    state["phase"] = 9
                elif phase == 9:
                    if state["iphone_window"].state != "streaming":
                        return GLib.SOURCE_CONTINUE
                    app.remove_device(state["iphone"].id)
                    state["phase"] = 10
                elif phase == 10:
                    if app.device_transports:
                        return GLib.SOURCE_CONTINUE
                    assert not (app.data_dir / "devices" / state["iphone"].id).exists()
                    assert len(app.devices.all()) == 1
                    assert app.library.search("history") == []
                    (root / "devices.json").write_text(
                        json.dumps(
                            [
                                ["synthetic-phone", "Synthetic Phone", "device"],
                                ["another-synthetic", "Another phone", "unauthorized"],
                            ]
                        )
                    )
                    state["phase"] = 11
                elif phase == 11:
                    if len(app.device_tiles()) != 2:
                        return GLib.SOURCE_CONTINUE
                    assert len(app.devices.all()) == 1
                    transient, status = app.device_tiles()[1]
                    assert transient.name == "Another phone" and status == "Allow USB debugging"
                    state["manager"] = DevicesDialog(state["home"])
                    assert len(state["manager"].detected_rows) == 1
                    (root / "devices.json").write_text("[]")
                    state["phase"] = 12
                elif phase == 12:
                    if app.connected_android:
                        return GLib.SOURCE_CONTINUE
                    assert len(app.device_tiles()) == 1
                    assert app.device_tiles()[0][1] == "Disconnected"
                    assert state["manager"].detected_rows == []
                    state["manager"].force_close()
                    app.discovery.stop()
                    app.discovery.thread.join(timeout=3)
                    assert app.discovery.stopped.is_set()
                    for filename in root.glob("*.pid"):
                        try:
                            os.kill(int(filename.read_text()), 0)
                        except ProcessLookupError:
                            continue
                        raise AssertionError("A synthetic helper survived session cleanup")
                    for window in app.get_windows():
                        window.close()
                    state["completed"] = True
                    print(
                        "PASS: automatic phones, bundled server, Settings, "
                        "streams, input and cleanup"
                    )
                    app.quit()
                    return GLib.SOURCE_REMOVE
            except Exception as exc:
                errors.append(str(exc))
                traceback.print_exc()
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        with patch.dict(os.environ, fake_helpers(root)):
            GLib.timeout_add(50, tick)
            result = app.run(["mini-browser-phone-smoke"])
        return 1 if errors or not state["completed"] else result


if __name__ == "__main__":
    raise SystemExit(run())
