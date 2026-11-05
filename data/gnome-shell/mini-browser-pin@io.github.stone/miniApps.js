import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const CATEGORIES = [['all', 'All Apps'], ['websites', 'Websites'], ['scripts', 'Scripts']];

function verticalBox(styleClass) {
    const box = new St.BoxLayout({style_class: styleClass});
    if ('orientation' in box)
        box.orientation = Clutter.Orientation.VERTICAL;
    else
        box.vertical = true;
    return box;
}

export default class MiniApps {
    constructor(extension) {
        this.path = extension.path;
    }

    enable() {
        const [, contents] = Gio.File.new_for_path(`${this.path}/launcher.json`).load_contents(null);
        this._command = JSON.parse(new TextDecoder().decode(contents)).command;
        if (!Array.isArray(this._command) || !this._command.length ||
            !this._command.every(argument => typeof argument === 'string' && argument.length))
            throw new Error('MiniApps needs a valid Mini Browser launcher command.');
        this._apps = [];
        this._category = 'all';
        this._process = null;
        this._cancellable = null;
        this._launches = new Set();
        this._button = new PanelMenu.Button(0, 'MiniApps');
        this._button.menu.setSourceAlignment(0);
        this._button.add_child(new St.Label({
            text: 'MiniApps', y_align: Clutter.ActorAlign.CENTER,
        }));
        this._button.menu.actor.add_style_class_name('mini-apps-menu');

        const content = new St.BoxLayout({style_class: 'mini-apps-content'});
        const categories = verticalBox('mini-apps-categories');
        this._categories = new Map();
        for (const [id, title] of CATEGORIES) {
            const button = new St.Button({
                accessible_name: title, can_focus: true, track_hover: true,
                x_align: Clutter.ActorAlign.FILL,
                style_class: 'popup-menu-item mini-apps-category',
            });
            button.set_child(new St.Label({
                text: title, x_expand: true, x_align: Clutter.ActorAlign.START,
            }));
            button.connect('clicked', () => this._selectCategory(id));
            button.connect('notify::hover', () => {
                if (button.hover)
                    this._selectCategory(id);
            });
            button.connect('key-focus-in', () => this._selectCategory(id));
            categories.add_child(button);
            this._categories.set(id, button);
        }
        content.add_child(categories);
        this._list = verticalBox('mini-apps-list');
        this._scroll = new St.ScrollView({
            style_class: 'mini-apps-scroll', x_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        // St.ScrollView gained set_child() after GNOME 45.
        if (this._scroll.set_child)
            this._scroll.set_child(this._list);
        else
            this._scroll.add_actor(this._list);
        content.add_child(this._scroll);
        this._button.menu.box.add_child(content);
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const open = this._button.menu.addAction('Open Mini Browser', () => this._launch([]));
        open.accessible_name = 'Open Mini Browser';
        this._button.menu.connect('open-state-changed', (_menu, isOpen) => {
            if (isOpen)
                this._refresh();
        });
        Main.panel.addToStatusArea('miniApps', this._button, 2, 'left');
        this._selectCategory('all');
    }

    _selectCategory(category) {
        this._category = category;
        for (const [id, button] of this._categories) {
            if (id === category)
                button.add_style_pseudo_class('checked');
            else
                button.remove_style_pseudo_class('checked');
        }
        this._render();
    }

    _status(message) {
        this._list.destroy_all_children();
        const label = new St.Label({text: message, style_class: 'mini-apps-status'});
        label.clutter_text.line_wrap = true;
        this._list.add_child(label);
    }

    _render() {
        this._list.destroy_all_children();
        const apps = this._apps.filter(app =>
            this._category === 'all' || app.category === this._category);
        if (!apps.length) {
            this._status('No apps in this category.');
            return;
        }
        for (const app of apps) {
            const row = new St.Button({
                name: app.id, accessible_name: app.title, can_focus: true, track_hover: true,
                style_class: 'popup-menu-item mini-apps-app',
                x_align: Clutter.ActorAlign.FILL, x_expand: true,
            });
            const body = new St.BoxLayout({
                style_class: 'mini-apps-app-content', x_expand: true,
            });
            if (app.icon) {
                body.add_child(new St.Icon({
                    gicon: new Gio.FileIcon({file: Gio.File.new_for_path(app.icon)}),
                    icon_size: 32,
                }));
            } else if (app.category === 'scripts') {
                body.add_child(new St.Icon({icon_name: 'utilities-terminal-symbolic', icon_size: 32}));
            } else {
                const badge = new St.Bin({
                    style_class: `mini-apps-initial mini-apps-color${app.avatar.color}`,
                });
                badge.set_child(new St.Label({
                    text: app.avatar.initials,
                    x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
                }));
                body.add_child(badge);
            }
            const label = new St.Label({
                text: app.title, x_expand: true, y_align: Clutter.ActorAlign.CENTER,
            });
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            body.add_child(label);
            row.set_child(body);
            row.connect('key-focus-in', () => {
                const adjustment = this._adjustment();
                const box = row.get_allocation_box();
                if (box.y1 < adjustment.value)
                    adjustment.value = box.y1;
                else if (box.y2 > adjustment.value + adjustment.page_size)
                    adjustment.value = box.y2 - adjustment.page_size;
            });
            row.connect('clicked', () => this._launch(['--launch-app', app.id]));
            this._list.add_child(row);
        }
        this._adjustment().value = 0;
    }

    _adjustment() {
        return this._scroll.vadjustment ?? this._scroll.vscroll.adjustment;
    }

    _refresh() {
        // Only one small asynchronous catalog read per menu opening, never a poll.
        this._cancelRead();
        this._status('Loading apps…');
        const cancellable = this._cancellable = new Gio.Cancellable();
        let process;
        try {
            process = this._process = Gio.Subprocess.new(
                [...this._command, 'list-apps'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch {
            this._status('Mini Browser is unavailable. Reinstall it and try again.');
            return;
        }
        process.communicate_utf8_async(null, cancellable, (source, result) => {
            try {
                const [, output] = source.communicate_utf8_finish(result);
                if (cancellable.is_cancelled())
                    return;
                this._process = this._cancellable = null;
                if (!source.get_successful())
                    throw new Error('Catalog read failed.');
                const records = JSON.parse(output);
                if (!Array.isArray(records) || !records.every(app =>
                    typeof app.id === 'string' && typeof app.title === 'string' &&
                    ['websites', 'scripts'].includes(app.category)))
                    throw new Error('Invalid catalog.');
                this._apps = records;
                this._render();
            } catch {
                if (!cancellable.is_cancelled()) {
                    this._process = this._cancellable = null;
                    this._apps = [];
                    this._status('Could not load apps. Open Mini Browser and try again.');
                }
            }
        });
    }

    _launch(arguments_) {
        this._button.menu.close();
        try {
            const process = Gio.Subprocess.new([...this._command, ...arguments_],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
            const cancellable = new Gio.Cancellable();
            this._launches.add(cancellable);
            process.wait_check_async(cancellable, (source, result) => {
                this._launches.delete(cancellable);
                try {
                    source.wait_check_finish(result);
                } catch {
                    if (!cancellable.is_cancelled())
                        Main.notify('MiniApps', 'Could not open this app. Open Mini Browser and try again.');
                }
            });
        } catch {
            Main.notify('MiniApps', 'Mini Browser is unavailable. Reinstall it and try again.');
        }
    }

    _cancelRead() {
        this._cancellable?.cancel();
        this._process?.force_exit();
        this._cancellable = this._process = null;
    }

    disable() {
        this._cancelRead();
        for (const cancellable of this._launches ?? [])
            cancellable.cancel();
        this._button?.destroy();
        this._button = this._list = this._scroll = this._categories = this._apps = null;
    }
}
