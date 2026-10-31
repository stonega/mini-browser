"""Native management window for unpacked Chrome extensions."""

from sqlite3 import Error as DatabaseError
from threading import Thread

from .gtk import Adw, Gio, GLib, Gtk


class ExtensionsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Extensions", default_width=420, default_height=780)
        self._closing = False
        self.extensions_page = ExtensionsPage(self)
        self.toasts = Adw.ToastOverlay(child=self.extensions_page)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)
        close = Gio.SimpleAction.new("close", None)
        close.connect("activate", lambda *_: self.close())
        self.add_action(close)
        self.connect("close-request", self._close_requested)

    def toast(self, message):
        if not self._closing:
            self.toasts.add_toast(Adw.Toast(title=message))

    def _close_requested(self, *_):
        self._closing = True
        app = self.get_application()
        if app.extensions_window is self:
            app.extensions_window = None
        return False


class ExtensionsPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window = window
        self.app = window.get_application()
        self.loading = False
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=8,
            margin_bottom=24,
        )
        self.set_child(
            Adw.Clamp(
                child=body,
                maximum_size=600,
                tightening_threshold=600,
                margin_start=16,
                margin_end=16,
            )
        )
        body.append(Gtk.Label(label="Chrome extensions", xalign=0, css_classes=["heading"]))
        body.append(
            Gtk.Label(
                label="Load an unpacked Manifest V3 extension folder containing manifest.json. "
                "Keep the folder in place. Changes take effect after restarting Mini Browser.",
                wrap=True,
                xalign=0,
            )
        )
        body.append(
            Gtk.Label(
                label="Experimental support for content scripts and background features. "
                "Popups open in separate windows. Chrome Web Store installation and full "
                "Chrome tab/window integration are unavailable. "
                "Only load extensions you trust with your browsing data.",
                wrap=True,
                xalign=0,
                css_classes=["caption", "dim-label"],
            )
        )
        self.load_button = Gtk.Button(
            label="Load unpacked…", halign=Gtk.Align.START, css_classes=["suggested-action"]
        )
        self.load_button.connect("clicked", self._choose_folder)
        body.append(self.load_button)
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False, css_classes=["error"])
        body.append(self.status)
        self.restart_notice = Gtk.Label(
            label="Restart Mini Browser to apply extension changes.",
            wrap=True,
            xalign=0,
            visible=False,
            css_classes=["heading"],
        )
        body.append(self.restart_notice)
        self.empty = Gtk.Label(label="No extensions added", css_classes=["dim-label"])
        body.append(self.empty)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        body.append(self.rows)
        self.refresh()

    def _choose_folder(self, _button):
        picker = Gtk.FileDialog(title="Choose an unpacked Chrome extension")

        def selected(picker, result):
            try:
                folder = picker.select_folder_finish(result)
                path = folder.get_path()
                if not path:
                    raise ValueError("Choose a local unpacked extension folder.")
                if not self.window._closing:
                    self.add_folder(path)
            except GLib.Error:
                pass  # The file picker was dismissed.
            except ValueError as error:
                self._error(str(error))

        picker.select_folder(self.window, None, selected)

    def _error(self, message):
        self.status.set_label(message)
        self.status.set_visible(True)

    def _change(self, operation):
        try:
            operation()
        except ValueError as error:
            self._error(str(error))
            return False
        except (OSError, DatabaseError):
            self._error("Could not save the extension change. Check available space and try again.")
            return False
        self._saved()
        return True

    def _saved(self):
        if not self.window._closing:
            self.status.set_visible(False)
        self.app.extensions_changed = True
        for window in self.app.get_windows():
            if not getattr(window, "_closing", True) and hasattr(window, "extensions_page"):
                window.extensions_page.refresh()

    def add_folder(self, path):
        if self.loading:
            return
        self.loading = True
        self.load_button.set_sensitive(False)
        self.app.hold()

        def finished(message, saved_path):
            self.loading = False
            try:
                if not self.app._shutting_down:
                    if not self.window._closing:
                        self.load_button.set_sensitive(True)
                        if message:
                            self._error(message)
                    if not message:
                        self.app.extension_errors.pop(saved_path, None)
                        self._saved()
            finally:
                self.app.release()
            return GLib.SOURCE_REMOVE

        def work():
            message = saved_path = None
            try:
                saved_path = self.app.extensions.add(path)
            except ValueError as error:
                message = str(error)
            except (OSError, DatabaseError):
                message = "Could not save the extension. Check available space and try again."
            GLib.idle_add(finished, message, saved_path)

        Thread(target=work, daemon=True, name="extension-registration").start()

    def _toggle(self, switch, _parameter, path):
        def change():
            self.app.extensions.set_enabled(path, switch.get_active())
            self.app.extension_errors.pop(path, None)

        if not self._change(change):
            # Restore the persisted value after a failed validation/write.
            switch.handler_block_by_func(self._toggle)
            switch.set_active(not switch.get_active())
            switch.handler_unblock_by_func(self._toggle)

    def _open_popup(self, _button, path):
        try:
            self.app.open_extension_popup(path)
        except ValueError as error:
            self._error(str(error))
        except (OSError, DatabaseError):
            self._error("Could not open the popup. Check the browser data folder and try again.")

    def refresh(self):
        self.rows.remove_all()
        records = self.app.extensions.all()
        self.empty.set_visible(not records)
        self.rows.set_visible(bool(records))
        self.restart_notice.set_visible(self.app.extensions_changed)
        for record in records:
            row = Adw.ActionRow(
                title=record["name"],
                subtitle=f"Version {record['version']} · {record['path']}",
                title_lines=1,
                subtitle_lines=2,
                use_markup=False,
                tooltip_text=record["path"],
            )
            error = self.app.extension_errors.get(record["path"])
            if error:
                row.set_subtitle(error)
                row.add_css_class("error")
            launcher = self.app.extension_launchers.get(record["path"])
            if launcher is None or launcher["popup"]:
                open_popup = Gtk.Button(
                    label="Open popup",
                    tooltip_text="Open popup in its own window"
                    if launcher
                    else "Restart Mini Browser to open this extension's popup",
                    valign=Gtk.Align.CENTER,
                    sensitive=bool(launcher and launcher["popup"] and record["enabled"]),
                )
                open_popup.connect("clicked", self._open_popup, record["path"])
                row.add_suffix(open_popup)
            switch = Gtk.Switch(
                active=bool(record["enabled"]),
                valign=Gtk.Align.CENTER,
                tooltip_text="Enable extension after restart",
            )
            switch.update_property([Gtk.AccessibleProperty.LABEL], [f"Enable {record['name']}"])
            switch.connect("notify::active", self._toggle, record["path"])
            row.add_suffix(switch)
            remove = Gtk.Button(
                icon_name="user-trash-symbolic",
                tooltip_text=f"Remove {record['name']}",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
            )
            remove.connect(
                "clicked",
                lambda _button, path=record["path"]: self._change(
                    lambda: self.app.extensions.remove(path)
                ),
            )
            row.add_suffix(remove)
            self.rows.append(row)
