// Test-only extension, installed exclusively in the disposable GNOME desktop.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const NAME = 'io.github.stone.MiniBrowser.TestPointer';
const PATH = '/io/github/stone/MiniBrowser/TestPointer';
const XML = `<node><interface name="${NAME}">
    <method name="Frame">
        <arg type="o" direction="in"/>
        <arg type="i" direction="out"/><arg type="i" direction="out"/>
        <arg type="i" direction="out"/><arg type="i" direction="out"/>
    </method>
    <method name="Pointer">
        <arg type="o" direction="in"/>
        <arg type="d" direction="in"/><arg type="d" direction="in"/>
        <arg type="d" direction="in"/><arg type="d" direction="in"/>
    </method>
    <method name="Hold">
        <arg type="o" direction="in"/>
        <arg type="d" direction="in"/><arg type="d" direction="in"/>
        <arg type="u" direction="in"/>
    </method>
    <method name="Click">
        <arg type="o" direction="in"/>
        <arg type="d" direction="in"/><arg type="d" direction="in"/>
        <arg type="u" direction="in"/>
    </method>
    <method name="Flags">
        <arg type="o" direction="in"/>
        <arg type="b" direction="out"/><arg type="b" direction="out"/>
    </method>
    <method name="SetFlags">
        <arg type="o" direction="in"/>
        <arg type="b" direction="in"/><arg type="b" direction="in"/>
        <arg type="b" direction="out"/><arg type="b" direction="out"/>
    </method>
</interface></node>`;

export default class PointerTests extends Extension {
    enable() {
        const backend = global.stage.get_context
            ? global.stage.get_context().get_backend()
            : Clutter.get_default_backend();
        this._pointer = backend.get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        // GTK receives native focus only when the headless seat has a keyboard.
        this._keyboard = backend.get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._export = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._export.export(Gio.DBus.session, PATH);
        this._name = Gio.bus_own_name_on_connection(
            Gio.DBus.session, NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._timer = 0;
        this._pressed = false;
    }

    _window(path, invocation) {
        const window = global.get_window_actors().map(actor => actor.meta_window).find(w =>
            ['MediaSmoke', 'PinSmoke', 'EngineSmoke', 'Smoke', 'AppsSmoke', 'ExtensionSmoke'].some(kind =>
                w.get_gtk_application_id()?.startsWith(`io.github.stone.MiniBrowser.${kind}.`)) &&
            w.get_gtk_unique_bus_name() === invocation.get_sender() &&
            w.get_gtk_window_object_path() === path);
        if (!window)
            invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownObject', 'No owned test window.');
        return window;
    }

    FrameAsync([path], invocation) {
        const window = this._window(path, invocation);
        if (!window)
            return;
        const rect = window.get_frame_rect();
        invocation.return_value(new GLib.Variant('(iiii)', [rect.x, rect.y, rect.width, rect.height]));
    }

    FlagsAsync([path], invocation) {
        const window = this._window(path, invocation);
        if (window)
            invocation.return_value(new GLib.Variant('(bb)', [window.is_above(), window.is_on_all_workspaces()]));
    }

    SetFlagsAsync([path, above, sticky], invocation) {
        const window = this._window(path, invocation);
        if (!window)
            return;
        if (above)
            window.make_above();
        else
            window.unmake_above();
        if (sticky)
            window.stick();
        else
            window.unstick();
        invocation.return_value(new GLib.Variant('(bb)', [window.is_above(), window.is_on_all_workspaces()]));
    }

    PointerAsync([path, x, y, dx, dy], invocation) {
        this._interact(path, x, y, dx, dy, 0, invocation);
    }

    HoldAsync([path, x, y, duration], invocation) {
        this._interact(path, x, y, 0, 0, duration, invocation);
    }

    ClickAsync([path, x, y, button], invocation) {
        this._interact(path, x, y, 0, 0, 0, invocation, button);
    }

    _interact(path, x, y, dx, dy, hold, invocation, button = 1) {
        const window = this._window(path, invocation);
        if (!window)
            return;
        const rect = window.get_frame_rect();
        if (this._timer || ![1, 3].includes(button) || ![x, y, dx, dy].every(Number.isFinite) ||
            x < 0 || y < 0 || x >= rect.width || y >= rect.height ||
            Math.abs(dx) > 150 || Math.abs(dy) > 150 || hold > 2000) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.InvalidArgs', 'Invalid test interaction.');
            return;
        }
        const motion = fraction => this._pointer.notify_absolute_motion(
            GLib.get_monotonic_time(), rect.x + x + dx * fraction, rect.y + y + dy * fraction);
        Main.overview.hide();
        window.activate(global.get_current_time());
        motion(0);
        let step = 0;
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 80, () => {
            step++;
            if (step === 5 && hold > 0) {
                hold -= 80;
                step--;
                return GLib.SOURCE_CONTINUE;
            }
            if (step < 4)
                motion(0);
            else if (step === 4) {
                this._pressed = button;
                this._pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.PRESSED);
            }
            else if (step < 8)
                motion((step - 4) / 3);
            else if (step === 8) {
                this._pressed = false;
                this._pointer.notify_button(GLib.get_monotonic_time(), button, Clutter.ButtonState.RELEASED);
            }
            else {
                this._timer = 0;
                invocation.return_value(new GLib.Variant('()', []));
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._timer)
            GLib.source_remove(this._timer);
        if (this._pressed)
            this._pointer.notify_button(GLib.get_monotonic_time(), this._pressed, Clutter.ButtonState.RELEASED);
        Gio.bus_unown_name(this._name);
        this._export.unexport();
        this._keyboard = this._pointer = this._export = null;
    }
}
