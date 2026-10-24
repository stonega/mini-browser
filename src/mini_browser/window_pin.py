"""Always-on-top control through the scoped GNOME Shell companion extension."""

from .gtk import Gio, GLib

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
        button.connect("clicked", lambda *_: self.set_pinned(button.get_active()))
        window.connect("map", lambda *_: self._sync())
        self._render()

    def _render(self):
        self.button.set_active(self.pinned)
        self.button.set_sensitive(self.available and not self.pending)
        self.button.set_tooltip_text(
            "Unpin from top"
            if self.available and self.pinned
            else "Pin on top"
            if self.available
            else "Enable the Mini Browser Pin GNOME Shell extension to pin on top"
        )

    def _appeared(self, *_):
        self.available = True
        self._sync()

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
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        Gio.bus_unwatch_name(self._watch)
        self.connection.signal_unsubscribe(self._subscription)
