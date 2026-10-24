import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.stone.MiniBrowser.WindowPin';
const OBJECT_PATH = '/io/github/stone/MiniBrowser/WindowPin';
const APP_ID = 'io.github.stone.MiniBrowser';
const INTERFACE = `<node><interface name="${BUS_NAME}">
    <method name="GetPinned">
        <arg type="o" direction="in" name="window"/>
        <arg type="b" direction="out" name="pinned"/>
    </method>
    <method name="SetPinned">
        <arg type="o" direction="in" name="window"/>
        <arg type="b" direction="in" name="pinned"/>
        <arg type="b" direction="out" name="actual"/>
    </method>
    <signal name="PinnedChanged"><arg type="o"/><arg type="b"/></signal>
</interface></node>`;

export default class MiniBrowserPin extends Extension {
    enable() {
        this._windows = new Map();
        this._export = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._export.export(Gio.DBus.session, OBJECT_PATH);
        this._name = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    _find(path, invocation) {
        const sender = invocation.get_sender();
        const window = global.get_window_actors().map(actor => actor.meta_window).find(w => {
            const app = w.get_gtk_application_id();
            return (app === APP_ID || app?.startsWith(`${APP_ID}.`)) &&
                w.get_gtk_unique_bus_name() === sender &&
                w.get_gtk_window_object_path() === path;
        });
        if (!window) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownObject',
                'No Mini Browser window owned by the caller matches this path.');
            return null;
        }
        if (!this._windows.has(window)) {
            const entry = {
                above: window.is_above(),
                sticky: window.is_on_all_workspaces(),
                managed: false,
            };
            const changed = () => {
                Gio.DBus.session.emit_signal(sender, OBJECT_PATH, BUS_NAME,
                    'PinnedChanged', new GLib.Variant('(ob)', [path, this._pinned(window)]));
            };
            entry.changed = ['notify::above', 'notify::on-all-workspaces'].map(
                signal => window.connect(signal, changed));
            entry.closed = window.connect('unmanaged', () => this._forget(window));
            this._windows.set(window, entry);
        }
        return window;
    }

    GetPinnedAsync([path], invocation) {
        const window = this._find(path, invocation);
        if (window)
            invocation.return_value(new GLib.Variant('(b)', [this._pinned(window)]));
    }

    SetPinnedAsync([path, pinned], invocation) {
        const window = this._find(path, invocation);
        if (!window)
            return;
        this._windows.get(window).managed = true;
        this._apply(window, pinned, pinned);
        invocation.return_value(new GLib.Variant('(b)', [this._pinned(window)]));
    }

    _pinned(window) {
        return window.is_above() && window.is_on_all_workspaces();
    }

    _apply(window, above, sticky) {
        if (above)
            window.make_above();
        else
            window.unmake_above();
        if (sticky)
            window.stick();
        else
            window.unstick();
    }

    _forget(window) {
        const entry = this._windows.get(window);
        for (const signal of entry.changed)
            window.disconnect(signal);
        window.disconnect(entry.closed);
        this._windows.delete(window);
    }

    disable() {
        Gio.bus_unown_name(this._name);
        this._export.unexport();
        for (const [window, entry] of this._windows) {
            this._forget(window);
            if (entry.managed)
                this._apply(window, entry.above, entry.sticky);
        }
        this._windows = null;
        this._export = null;
    }
}
