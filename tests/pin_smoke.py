"""Verify compositor pin state, caller ownership, and extension lifecycle."""

import os
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Adw, Gio, GLib, Gtk
from mini_browser.window_pin import BUS_NAME, EXTENSION_UUID, INTERFACE, OBJECT_PATH


def click_response(widget, label):
    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        widget.emit("clicked")
        return True
    child = widget.get_first_child()
    while child:
        if click_response(child, label):
            return True
        child = child.get_next_sibling()
    return False


def window_flags(window, desired=None):
    """Read or change independent Mutter flags through the isolated test helper."""
    interface = "io.github.stone.MiniBrowser.TestPointer"
    return (
        window.get_application()
        .get_dbus_connection()
        .call_sync(
            interface,
            "/io/github/stone/MiniBrowser/TestPointer",
            interface,
            "Flags" if desired is None else "SetFlags",
            GLib.Variant("(o)", (window.pin.path,))
            if desired is None
            else GLib.Variant("(obb)", (window.pin.path, *desired)),
            GLib.VariantType.new("(bb)"),
            Gio.DBusCallFlags.NONE,
            1500,
            None,
        )
        .unpack()
    )


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-pin-") as temporary:
        root = Path(temporary)
        app = BrowserApplication(
            discover_devices=False,
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.PinSmoke.Run{os.getpid()}",
        )
        state = {"completed": False}
        errors = []

        def ready(window):
            return window.pin.available and not window.pin.pending and not window.pin._enabling

        def extension(enabled):
            return app.get_dbus_connection().call_sync(
                "org.gnome.Shell",
                "/org/gnome/Shell",
                "org.gnome.Shell.Extensions",
                "EnableExtension" if enabled else "DisableExtension",
                GLib.Variant("(s)", (EXTENSION_UUID,)),
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )

        def scenario(window):
            yield lambda: ready(window)
            other = app.new_window()
            yield lambda: ready(other)
            assert not window.pin.pinned and not other.pin.pinned
            window.pin_button.emit("clicked")
            yield lambda: ready(window) and window.pin.pinned
            assert not other.pin.pinned
            assert window_flags(window) == (True, True)
            assert window_flags(other) == (False, False)

            # GNOME's window menu can change either setting independently.
            window_flags(window, (True, False))
            yield lambda: not window.pin.pinned and not window.pin_button.get_active()
            window_flags(window, (True, True))
            yield lambda: window.pin.pinned and window.pin_button.get_active()
            window_flags(window, (False, True))
            yield lambda: not window.pin.pinned and not window.pin_button.get_active()
            window.pin_button.emit("clicked")
            yield lambda: ready(window) and window.pin.pinned
            assert window_flags(window) == (True, True)
            window.pin_button.emit("clicked")
            yield lambda: ready(window) and not window.pin.pinned
            assert window_flags(window) == (False, False)
            window.pin_button.emit("clicked")
            yield lambda: ready(window) and window.pin.pinned

            # A separate bus client cannot control this app's windows.
            outsider = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            try:
                try:
                    outsider.call_sync(
                        BUS_NAME,
                        OBJECT_PATH,
                        INTERFACE,
                        "SetPinned",
                        GLib.Variant("(ob)", (window.pin.path, False)),
                        GLib.VariantType.new("(b)"),
                        Gio.DBusCallFlags.NONE,
                        1500,
                        None,
                    )
                except GLib.Error as error:
                    assert (
                        Gio.DBusError.get_remote_error(error)
                        == "org.freedesktop.DBus.Error.UnknownObject"
                    )
                else:
                    raise AssertionError("Pin service accepted an unrelated bus caller")
            finally:
                outsider.close_sync(None)

            # Bypass the controller to verify real compositor change notifications.
            app.get_dbus_connection().call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "SetPinned",
                GLib.Variant("(ob)", (window.pin.path, False)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
            yield lambda: not window.pin.pinned and not window.pin_button.get_active()
            window.pin.set_pinned(True)
            window.pin.set_pinned(False)
            window.pin.set_pinned(True)
            yield lambda: ready(window) and window.pin.pinned
            extension(False)
            yield lambda: not window.pin.available
            assert window_flags(window) == (False, False)
            assert window.pin_button.get_sensitive()
            assert not window.pin_button.get_active()
            with patch(
                "mini_browser.window_pin.EXTENSION_UUID", "missing-pin@mini-browser.invalid"
            ):
                window.pin_button.emit("clicked")
                dialog = window.get_visible_dialog()
                assert isinstance(dialog, Adw.AlertDialog)
                assert dialog.get_heading() == "Enable window pinning"
                assert click_response(dialog, "Enable Pinning")
                yield (
                    lambda: (
                        window.get_visible_dialog() is not None
                        and window.get_visible_dialog().get_heading() == "Finish setting up pinning"
                    )
                )
                assert "sign out and back in" in window.get_visible_dialog().get_body()
                window.get_visible_dialog().force_close()
            window.pin_button.emit("clicked")
            dialog = window.get_visible_dialog()
            assert dialog.get_heading() == "Enable window pinning"
            assert click_response(dialog, "Enable Pinning")
            yield lambda: ready(window) and window.pin.pinned
            assert window.pin_button.get_sensitive()
            window.pin.set_pinned(False)
            yield lambda: ready(window) and not window.pin.pinned
            assert window.pin_button.get_sensitive()
            assert window_flags(window) == (False, False)
            assert not other.pin.pinned

            # Extension shutdown restores each original flag, including mixed states.
            extension(False)
            yield lambda: not window.pin.available
            above_only, sticky_only = app.new_window(), app.new_window()
            yield lambda: all(
                probe.get_mapped() and probe.pin.path for probe in (above_only, sticky_only)
            )
            window_flags(above_only, (True, False))
            window_flags(sticky_only, (False, True))
            extension(True)
            yield lambda: all(ready(probe) for probe in (window, other, above_only, sticky_only))
            assert not above_only.pin.pinned and not sticky_only.pin.pinned
            for probe in (above_only, sticky_only):
                probe.pin.set_pinned(True)
            yield lambda: all(
                ready(probe) and probe.pin.pinned for probe in (above_only, sticky_only)
            )
            assert window_flags(above_only) == window_flags(sticky_only) == (True, True)
            extension(False)
            yield lambda: not window.pin.available
            assert window_flags(above_only) == (True, False)
            assert window_flags(sticky_only) == (False, True)
            above_only.close()
            sticky_only.close()
            extension(True)
            yield lambda: ready(window) and ready(other)
            window.close()
            other.close()
            state["completed"] = True
            print(
                "PASS: above/all-workspace pins, independent windows, caller ownership, "
                "independent compositor signals, mixed-state restoration, "
                "rapid requests, extension disable/re-enable, missing-extension guidance, "
                "enable from pin button"
            )

        def tick():
            try:
                if "steps" not in state:
                    if not app.get_windows():
                        return GLib.SOURCE_CONTINUE
                    state["steps"] = scenario(app.get_windows()[0])
                    state["waiting"] = next(state["steps"])
                elif state["waiting"]():
                    state["waiting"] = next(state["steps"])
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception as error:
                errors.append(str(error))
                traceback.print_exc()
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            waiting = state.get("waiting")
            line = waiting.__code__.co_firstlineno if waiting else "startup"
            errors.append(f"Pin smoke timed out at condition on line {line}")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, tick)
        timer = GLib.timeout_add_seconds(20, timeout)
        try:
            app.run(["mini-browser-pin-smoke"])
        finally:
            if GLib.MainContext.default().find_source_by_id(timer):
                GLib.source_remove(timer)
        if errors or not state["completed"]:
            raise SystemExit("; ".join(errors) or "Pin smoke did not complete")


if __name__ == "__main__":
    run()
