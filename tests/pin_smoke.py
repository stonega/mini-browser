"""Verify compositor pin state, caller ownership, and extension lifecycle."""

import os
import tempfile
import traceback
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib
from mini_browser.window_pin import BUS_NAME, EXTENSION_UUID, INTERFACE, OBJECT_PATH


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-pin-") as temporary:
        root = Path(temporary)
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.PinSmoke.Run{os.getpid()}",
        )
        state = {"completed": False}
        errors = []

        def ready(window):
            return window.pin.available and not window.pin.pending

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
            assert not window.pin_button.get_sensitive()
            assert not window.pin_button.get_active()
            window.pin.set_pinned(False)
            extension(True)
            yield lambda: ready(window) and not window.pin.pinned
            assert window.pin_button.get_sensitive()
            assert not other.pin.pinned
            window.close()
            other.close()
            state["completed"] = True
            print(
                "PASS: independent window pins, caller ownership, compositor signals, "
                "rapid requests, extension disable/re-enable"
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
