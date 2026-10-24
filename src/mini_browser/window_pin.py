"""Always-on-top control through the scoped GNOME Shell companion extension."""

from .gtk import Adw, Gio, GLib

BUS_NAME = "io.github.stone.MiniBrowser.WindowPin"
OBJECT_PATH = "/io/github/stone/MiniBrowser/WindowPin"
INTERFACE = BUS_NAME
EXTENSION_UUID = "mini-browser-pin@io.github.stone"


class WindowPin:
    def __init__(self, window, button):
        self.window = window
        self.button = button
        self.available = False
        self.pinned = False
        self.requested = None
        self.pending = False
        self._enabling = False
        self._enable_timer = 0
        self._serial = 0
        self._retry = 0
        self._closed = False
        app = window.get_application()
        self.connection = app.get_dbus_connection()
        self.path = f"{app.get_dbus_object_path()}/window/{window.get_id()}"
        self._subscription = self.connection.signal_subscribe(
            BUS_NAME,
            INTERFACE,
            "PinnedChanged",
            OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._changed,
        )
        self._watch = Gio.bus_watch_name_on_connection(
            self.connection,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._appeared,
            self._vanished,
        )
        button.connect("clicked", self._clicked)
        window.connect("map", lambda *_: self._sync())
        self._render()

    def _render(self):
        self.button.set_active(self.pinned)
        self.button.set_sensitive(not self.pending and not self._enabling)
        self.button.set_tooltip_text(
            "Unpin from top"
            if self.available and self.pinned
            else "Pin on top"
            if self.available
            else "Set up pinning"
        )

    def _clicked(self, *_):
        if self.available:
            self.set_pinned(self.button.get_active())
            return
        self.button.set_active(False)
        dialog = Adw.AlertDialog(
            heading="Enable window pinning",
            body=(
                "Pinning needs the Mini Browser Pin GNOME extension. "
                "Enable it to keep this window on top."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("enable", "Enable Pinning")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("enable")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", lambda _dialog, response: self._enable() if response == "enable" else None
        )
        dialog.present(self.window)

    def _enable(self):
        if self._closed:
            return
        self.requested = True
        self._enabling = True
        self._render()

        def finished(connection, result):
            enabled = False
            try:
                enabled = connection.call_finish(result).unpack()[0]
            except GLib.Error:
                pass
            if self._closed:
                return
            if not enabled:
                self._enabling = False
                self._render()
                dialog = Adw.AlertDialog(
                    heading="Finish setting up pinning",
                    body=(
                        "GNOME has not loaded Mini Browser Pin. After installing the updated app, "
                        "sign out and back in once, then enable Mini Browser Pin in Extensions."
                    ),
                )
                dialog.add_response("close", "OK")
                dialog.present(self.window)
            else:
                self._wait_for_extension()

        self.connection.call(
            "org.gnome.Shell",
            "/org/gnome/Shell",
            "org.gnome.Shell.Extensions",
            "EnableExtension",
            GLib.Variant("(s)", (EXTENSION_UUID,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            1500,
            None,
            finished,
        )

    def _appeared(self, *_):
        self.available = True
        self._sync()

    def _wait_for_extension(self, attempt=0):
        self._enable_timer = 0
        if self._closed:
            return GLib.SOURCE_REMOVE
        if self.available or attempt >= 20:
            self._enabling = False
            self._render()
            if not self.available:
                dialog = Adw.AlertDialog(
                    heading="Pinning did not start",
                    body=(
                        "Turn on GNOME extensions and enable Mini Browser Pin in Extensions. "
                        "If you just installed it, sign out and back in once."
                    ),
                )
                dialog.add_response("close", "OK")
                dialog.present(self.window)
            return GLib.SOURCE_REMOVE
        self._enable_timer = GLib.timeout_add(100, self._wait_for_extension, attempt + 1)
        return GLib.SOURCE_REMOVE

    def _vanished(self, *_):
        self._serial += 1
        self.available = self.pending = self.pinned = False
        self._render()

    def _changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        path, pinned = parameters.unpack()
        if not self._closed and path == self.path:
            self.pinned = pinned
            if not self.pending:
                self.requested = pinned
            self._render()

    def set_pinned(self, pinned):
        self.requested = bool(pinned)
        self._sync()

    def _sync(self, attempt=0):
        if self._closed or not self.available or not self.window.get_mapped():
            return
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        self._serial += 1
        serial = self._serial
        self.pending = True
        self._render()
        desired = self.requested

        def finished(connection, result):
            try:
                pinned = connection.call_finish(result).unpack()[0]
            except GLib.Error as error:
                if self._closed or serial != self._serial:
                    return
                if (
                    Gio.DBusError.get_remote_error(error)
                    == "org.freedesktop.DBus.Error.UnknownObject"
                    and attempt < 10
                ):
                    # The compositor may receive GTK's window identity after map.
                    def retry():
                        self._retry = 0
                        self._sync(attempt + 1)
                        return GLib.SOURCE_REMOVE

                    self._retry = GLib.timeout_add(100, retry)
                    return
                self.pending = False
                self.requested = self.pinned
                self._render()
                self.window.toast(
                    "Could not change pin state. Check the Mini Browser Pin extension."
                )
                return
            if self._closed or serial != self._serial:
                return
            self.pending = False
            self.pinned = self.requested = pinned
            self._render()

        self.connection.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "GetPinned" if desired is None else "SetPinned",
            GLib.Variant("(o)", (self.path,))
            if desired is None
            else GLib.Variant("(ob)", (self.path, desired)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            1500,
            None,
            finished,
        )

    def close(self):
        self._closed = True
        self._serial += 1
        if self._enable_timer:
            GLib.source_remove(self._enable_timer)
            self._enable_timer = 0
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        Gio.bus_unwatch_name(self._watch)
        self.connection.signal_unsubscribe(self._subscription)
