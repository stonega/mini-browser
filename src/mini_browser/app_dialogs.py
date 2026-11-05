"""Native forms for saving website and command-backed Home apps."""

import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .dialogs import padded_box
from .gtk import Adw, GdkPixbuf, Gio, GLib, Gtk
from .navigation import is_web_url
from .site_icons import save_custom_icon
from .storage import app_launch_url, app_title


class AddAppDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title="Add app", content_width=380, content_height=500)
        self.window = window
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        box = padded_box()
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box))
        group = Adw.PreferencesGroup()
        self.kind = Adw.ComboRow(
            title="App type", model=Gtk.StringList.new(["Web app", "Script app"])
        )
        self.name = Adw.EntryRow(title="Name")
        self.url = Adw.EntryRow(title="Website URL")
        address = window.home_entry.get_text().strip()
        if is_web_url(address):
            self.url.set_text(address)
        for row in (self.kind, self.name, self.url):
            group.add(row)
        box.append(group)
        self.script_group = Adw.PreferencesGroup()
        self.command = Adw.EntryRow(title="Command")
        self.directory = Adw.EntryRow(title="Working folder", text=str(Path.home()))
        self.port = Adw.EntryRow(title="Port (empty for automatic)")
        self.script_group.add(self.command)
        self.script_group.add(self.directory)
        self.script_group.add(self.port)
        box.append(self.script_group)
        self.hint = Gtk.Label(
            label="Your command receives PORT. Leave the port empty to choose a free one. "
            "The app opens at http://127.0.0.1:PORT/. Closing its last window stops the command.",
            wrap=True,
            xalign=0,
            css_classes=["dim-label"],
        )
        box.append(self.hint)
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False, css_classes=["error"])
        box.append(self.status)
        self.submit = Gtk.Button(label="Add", css_classes=["suggested-action"])
        self.submit.connect("clicked", self._save)
        box.append(self.submit)
        self.kind.connect("notify::selected", self._kind_changed)
        self._kind_changed()
        self.set_default_widget(self.submit)
        self.set_focus(self.name)

    def _kind_changed(self, *_):
        script = self.kind.get_selected() == 1
        self.script_group.set_visible(script)
        self.hint.set_visible(script)
        self.url.set_visible(not script)
        self.status.set_visible(False)

    def _save(self, *_):
        app = self.window.get_application()
        try:
            if self.kind.get_selected() == 1:
                app.script_apps.save(
                    self.name.get_text(),
                    self.command.get_text(),
                    self.port.get_text(),
                    self.directory.get_text(),
                )
            else:
                app.library.add_home_app(self.url.get_text(), self.name.get_text().strip() or None)
        except ValueError as error:
            message = str(error)
        except (sqlite3.Error, OSError):
            message = (
                "Could not save this app. Check that its folder and the library are accessible."
            )
        else:
            self.window.refresh_app_windows()
            self.window.toast("App added")
            self.close()
            return
        self.status.set_text(message)
        self.status.set_visible(True)


class EditWebAppDialog(Adw.Dialog):
    def __init__(self, window, record):
        super().__init__(title="Edit app", content_width=380, content_height=520)
        self.window, self.record = window, record
        self._icon_data = None
        self._icon_reset = False
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.submit = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.submit.connect("clicked", self._save)
        header.pack_end(self.submit)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        box = padded_box()
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box))
        group = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title="Display title", text=record["title"])
        self.domain = Adw.ActionRow(title="Domain", subtitle=record["site"], use_markup=False)
        parts = urlsplit(record["url"])
        self.path = Adw.EntryRow(
            title="Default path",
            text=urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment)),
        )
        for row in (self.name, self.domain, self.path):
            group.add(row)
        box.append(group)
        box.append(
            Gtk.Label(
                label="For example, /dashboard or /app?view=home. The domain stays the same.",
                wrap=True,
                xalign=0,
                css_classes=["dim-label"],
            )
        )
        icons = Adw.PreferencesGroup(title="App icon")
        self.icon_row = Adw.ActionRow(
            title="Website icon", subtitle="Uses the site's app icon or favicon"
        )
        self.preview = Gtk.Image(pixel_size=44)
        self.icon_row.add_prefix(self.preview)
        icons.add(self.icon_row)
        box.append(icons)
        buttons = Gtk.Box(spacing=8, homogeneous=True)
        self.choose_icon = Gtk.Button(label="Choose image…")
        self.choose_icon.connect("clicked", self._choose_icon)
        self.reset_icon = Gtk.Button(label="Use website icon")
        self.reset_icon.connect("clicked", self._reset_icon)
        buttons.append(self.choose_icon)
        buttons.append(self.reset_icon)
        box.append(buttons)
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False, css_classes=["error"])
        box.append(self.status)
        for entry in (self.name, self.path):
            entry.connect("changed", lambda *_: self.status.set_visible(False))
        self._show_icon(record.get("icon"), custom=bool(record.get("icon")))
        self.set_default_widget(self.submit)
        self.set_focus(self.name)

    def _show_icon(self, path=None, *, custom=False):
        path = path or self.window.get_application().site_icons.path_for(self.record["url"])
        if path:
            self.preview.set_from_file(str(path))
        else:
            self.preview.set_from_icon_name("globe-symbolic")
        self.icon_row.set_title("Custom image" if custom else "Website icon")
        self.icon_row.set_subtitle("" if custom else "Uses the site's app icon or favicon")

    def _reset_icon(self, *_):
        self._icon_data, self._icon_reset = None, True
        self._show_icon()

    def set_icon_file(self, path):
        path = Path(path)
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Choose an image smaller than 4 MiB.")
        info, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
        if not info or not 0 < width <= 8192 or not 0 < height <= 8192:
            raise ValueError("Choose a PNG, JPEG, WebP, or ICO image up to 8192 × 8192 pixels.")
        image = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 256, 256, True)
        success, data = image.save_to_bufferv("png", [], [])
        if not success:
            raise ValueError("Could not read this image. Choose another file.")
        self._icon_data, self._icon_reset = bytes(data), False
        self.preview.set_from_pixbuf(image)
        self.icon_row.set_title("Custom image")
        self.icon_row.set_subtitle(path.name)
        self.status.set_visible(False)

    def _choose_icon(self, *_):
        file_filter = Gtk.FileFilter(name="App icons")
        for mime in (
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/x-icon",
            "image/vnd.microsoft.icon",
        ):
            file_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        picker = Gtk.FileDialog(title="Choose app icon", filters=filters)

        def selected(picker, result):
            try:
                file = picker.open_finish(result)
                path = file.get_path()
                if not path:
                    raise ValueError("Choose an image stored on this computer.")
                self.set_icon_file(path)
                self.status.set_visible(False)
            except GLib.Error as error:
                if error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    return
                self.status.set_text("Could not read this image. Choose another file.")
                self.status.set_visible(True)
            except (ValueError, OSError) as error:
                self.status.set_text(str(error))
                self.status.set_visible(True)

        picker.open(self.window, None, selected)

    def _save(self, *_):
        try:
            title = app_title(self.name.get_text())
            app_launch_url(self.record["url"], self.path.get_text())
            icon = "" if self._icon_reset else None
            if self._icon_data:
                icon = save_custom_icon(self.window.library, self._icon_data)
            self.window.library.edit_web_app(
                self.record["url"], title, self.path.get_text(), icon=icon
            )
        except ValueError as error:
            self.status.set_text(str(error))
        except (sqlite3.Error, OSError):
            self.status.set_text("Could not save this app. Check that the library is writable.")
        else:
            self.window.refresh_app_windows()
            self.window.toast("App updated")
            self.close()
            return
        self.status.set_visible(True)
