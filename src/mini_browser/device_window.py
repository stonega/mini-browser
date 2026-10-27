"""One native window per phone, with explicit session and input ownership."""

import secrets
import time

from .device_video import DeviceVideo
from .devices import KINDS, AndroidTransport, DeviceError, IPhoneTransport, phone_coordinates
from .gtk import Adw, Gdk, Gio, GLib, Gtk
from .window_pin import WindowPin
from .window_size import DEFAULT_SIZE, WindowSizeMemory

KEYS = {
    Gdk.KEY_BackSpace: "67",
    Gdk.KEY_Return: "66",
    Gdk.KEY_KP_Enter: "66",
    Gdk.KEY_Tab: "61",
    Gdk.KEY_Delete: "112",
    Gdk.KEY_Left: "21",
    Gdk.KEY_Right: "22",
    Gdk.KEY_Up: "19",
    Gdk.KEY_Down: "20",
}


class DeviceWindow(Adw.ApplicationWindow):
    def __init__(self, app, device):
        super().__init__(
            application=app,
            title=device.name,
            default_width=DEFAULT_SIZE[0],
            default_height=DEFAULT_SIZE[1],
        )
        self.device = device
        self._closing = False
        self.generation = 0
        self.transport = None
        self.video = None
        self.frame_size = (0, 0)
        self.native_size = (0, 0)
        self._press = None
        self.state = "disconnected"
        self.status_page = Adw.StatusPage(icon_name="phone-symbolic", title="Connecting to phone")
        self.reconnect_button = Gtk.Button(
            label="Reconnect", halign=Gtk.Align.CENTER, css_classes=["suggested-action"]
        )
        self.reconnect_button.connect("clicked", lambda *_: self.start())
        self.pairing_code = Gtk.Label(css_classes=["title-1"], selectable=True)
        self.instructions = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        actions.append(self.instructions)
        actions.append(self.pairing_code)
        actions.append(self.reconnect_button)
        self.status_page.set_child(actions)
        self.picture = Gtk.Picture(can_shrink=True, focusable=True, hexpand=True, vexpand=True)
        self.picture.add_css_class("phone-screen")
        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self.status_page, "status")
        self.stack.add_named(self.picture, "screen")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.title_widget = Adw.WindowTitle(title=device.name, subtitle=KINDS[device.kind])
        header = Adw.HeaderBar(title_widget=self.title_widget, decoration_layout=":close")
        self.pin_button = Gtk.ToggleButton(icon_name="view-pin-symbolic", tooltip_text="Pin on top")
        header.pack_end(self.pin_button)
        self.pin = WindowPin(self, self.pin_button)
        menu = Gio.Menu()
        menu.append("Reconnect", "win.reload")
        menu.append("Disconnect", "win.stop")
        if device.kind == "android":
            phone = Gio.Menu()
            phone.append("Phone Back", "win.phone-back")
            phone.append("Phone Home", "win.phone-home")
            phone.append("Phone app switcher", "win.phone-apps")
            menu.append_section(None, phone)
        menu.append("Settings", "win.settings")
        header.pack_end(
            Gtk.MenuButton(
                icon_name="open-menu-symbolic", tooltip_text="Phone menu", menu_model=menu
            )
        )
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)
        for name, callback in {
            "close": self.close,
            "reload": self.start,
            "stop": self.disconnect,
            "settings": self.settings,
            "phone-back": lambda: self.key("4"),
            "phone-home": lambda: self.key("3"),
            "phone-apps": lambda: self.key("187"),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self._pressed)
        drag.connect("drag-end", self._released)
        self.picture.add_controller(drag)
        back = Gtk.GestureClick(button=3)
        back.connect("released", lambda *_: self.key("4"))
        self.picture.add_controller(back)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        self.picture.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.picture.add_controller(keys)
        self.connect("close-request", self._close_requested)
        self.window_size = WindowSizeMemory(self, app.library)
        self.window_size.select(("device", device.id))
        self._set_input_enabled(False)
        GLib.idle_add(self._open)

    def _open(self):
        if not self._closing:
            self.start()
        return GLib.SOURCE_REMOVE

    def settings(self):
        from .device_dialogs import devices_dialog

        devices_dialog(self)

    def toast(self, message):
        if not self._closing:
            self.toasts.add_toast(Adw.Toast(title=message))

    def _set_input_enabled(self, enabled):
        for name in ("phone-back", "phone-home", "phone-apps"):
            self.lookup_action(name).set_enabled(enabled and self.device.kind == "android")

    def start(self):
        if self._closing:
            return
        self.disconnect()
        generation = self.generation
        self.state = "connecting"
        self.status_page.set_title("Connecting to phone")
        self.status_page.set_description(
            "Unlock your phone and approve USB debugging for this computer."
            if self.device.kind == "android"
            else "Starting the AirPlay receiver…"
        )
        self.instructions.set_label("")
        self.pairing_code.set_label("")
        self.reconnect_button.set_visible(False)
        app = self.get_application()

        def dispatch(callback, *args):
            def apply():
                if not self._closing and self.generation == generation:
                    callback(*args)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(apply)

        try:
            self.video = DeviceVideo(
                self.picture,
                self.device.kind,
                self._frame,
                lambda message: self._status("error", message),
                self._waiting,
            )

            def status(state, message):
                dispatch(self._status, state, message)

            def finished():
                GLib.idle_add(app.finish_device_transport, transport)

            if self.device.kind == "android":
                self.transport = AndroidTransport(
                    self.device,
                    self.video.feed,
                    status,
                    lambda size: dispatch(self._native_size, size),
                    finished,
                )
            else:
                pin = f"{secrets.randbelow(10000):04d}"
                self.pairing_code.set_label(pin)
                self.instructions.set_label(
                    f"On the same local network, choose “{self.device.receiver_name}” in "
                    "Control Center → Screen Mirroring. Enter this pairing code when asked. "
                    "Use the iPhone to control its screen."
                )
                self.transport = IPhoneTransport(
                    self.device,
                    app.data_dir / "devices" / self.device.id,
                    self.video.port,
                    pin,
                    status,
                    finished,
                )
            transport = self.transport
            app.device_transports.add(transport)
            app.hold()  # Cleanup must finish even after the last window closes.
            try:
                transport.start(
                    after=(
                        previous
                        for previous in app.device_transports
                        if previous is not transport and previous.device.id == self.device.id
                    )
                )
            except RuntimeError:
                app.finish_device_transport(transport)
                raise DeviceError(
                    "Could not start a phone session. Reconnect to try again."
                ) from None
        except (DeviceError, OSError) as exc:
            self._status(
                "error", str(exc) if isinstance(exc, DeviceError) else "Could not connect."
            )

    def _native_size(self, size):
        self.native_size = size

    def _frame(self, size):
        if self._closing or self.video is None:
            return
        if self.frame_size != size:
            self._press = None
        self.frame_size = size
        if self.state != "streaming":
            self.state = "streaming"
            self.title_widget.set_subtitle(
                "Android · mouse and keyboard"
                if self.device.kind == "android"
                else "iPhone · viewing"
            )
            self.pairing_code.set_label("")
            self.stack.set_visible_child_name("screen")
            self._set_input_enabled(True)
            self.picture.grab_focus()

    def _waiting(self):
        self._press = None
        self.state = "waiting"
        self.title_widget.set_subtitle("iPhone · waiting for screen")
        self.status_page.set_title("Waiting for iPhone")
        self.status_page.set_description(
            "The screen stopped arriving. Start Screen Mirroring again."
        )
        self.stack.set_visible_child_name("status")
        self.reconnect_button.set_visible(True)

    def _status(self, state, message):
        if state == "input-error":
            self.toast(message)
        elif state == "waiting":
            if self.state != "streaming":
                self.state = state
                self.status_page.set_title("Waiting for iPhone")
                self.status_page.set_description(message)
                self.title_widget.set_subtitle("iPhone · waiting for screen")
        else:
            self.disconnect(message, state=state)

    def disconnect(
        self, message="Select Reconnect to show your phone again.", *, state="disconnected"
    ):
        self.generation += 1
        self._press = None
        self._set_input_enabled(False)
        self.state = state
        if self.transport:
            self.transport.stop()
            self.transport = None
        if self.video:
            self.video.close()
            self.video = None
        self.picture.set_paintable(None)
        self.pairing_code.set_label("")
        self.frame_size = (0, 0)
        self.native_size = (0, 0)
        self.title_widget.set_subtitle(f"{KINDS[self.device.kind]} · {state}")
        self.status_page.set_title(
            "Could not connect" if state == "error" else "Phone disconnected"
        )
        self.status_page.set_description(message)
        self.stack.set_visible_child_name("status")
        self.reconnect_button.set_visible(True)

    def _point(self, x, y):
        return phone_coordinates(
            x,
            y,
            (self.picture.get_width(), self.picture.get_height()),
            self.frame_size,
            self.native_size,
        )

    def _input(self, *arguments):
        if (
            self.device.kind == "android"
            and self.state == "streaming"
            and self.transport
            and self.is_active()
        ):
            self.transport.send(*(str(arg) for arg in arguments))

    def key(self, keycode):
        self._input("keyevent", keycode)

    def _pressed(self, _gesture, x, y):
        self.picture.grab_focus()
        point = self._point(x, y)
        self._press = (x, y, point, time.monotonic()) if point else None

    def _released(self, _gesture, dx, dy):
        pressed, self._press = self._press, None
        if not pressed:
            return
        x, y, origin, started = pressed
        destination = self._point(x + dx, y + dy)
        if destination is None:
            return
        duration = min(3000, max(50, int((time.monotonic() - started) * 1000)))
        if abs(dx) + abs(dy) < 8 and duration < 500:
            self._input("tap", *origin)
        else:
            self._input("swipe", *origin, *destination, duration)

    def _scroll(self, _controller, dx, dy):
        width, height = self.picture.get_width(), self.picture.get_height()
        start = self._point(width / 2, height / 2)
        end = self._point(
            width * (0.5 - max(-0.25, min(0.25, dx * 0.08))),
            height * (0.5 - max(-0.25, min(0.25, dy * 0.08))),
        )
        if start and end:
            self._input("swipe", *start, *end, "150")
        return True

    def _key_pressed(self, _controller, keyval, _keycode, modifiers):
        if modifiers & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
        ):
            return False
        if keyval in KEYS:
            self.key(KEYS[keyval])
            return True
        codepoint = Gdk.keyval_to_unicode(keyval)
        if 32 <= codepoint <= 126:
            self._input("text", chr(codepoint))
            return True
        if codepoint:
            self.toast("Use the phone keyboard for non-ASCII text.")
        return False

    def _close_requested(self, *_):
        self.window_size.close()
        self._closing = True
        self.pin.close()
        self.disconnect()
        self.get_application().device_windows.pop(self.device.id, None)
        self.get_application().refresh_devices()
        return False
