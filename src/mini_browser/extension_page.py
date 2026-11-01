"""Native Installed and Market management for Chrome extensions."""

from sqlite3 import Error as DatabaseError
from threading import Thread

from .extension_catalog import MARKET_EXTENSIONS
from .gtk import Adw, Gio, GLib, Gtk, Pango


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


class ExtensionsPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.app = window.get_application()
        self.loading = False
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16,
        )
        self.append(
            Adw.Clamp(
                child=body,
                maximum_size=600,
                tightening_threshold=600,
                margin_start=10,
                margin_end=10,
            )
        )
        self.sections = Gtk.Stack(hhomogeneous=False, vhomogeneous=False, vexpand=True)
        self.tabs = Gtk.StackSwitcher(stack=self.sections)
        body.append(self.tabs)
        self.search_entry = Gtk.Entry(
            placeholder_text="Search extensions",
            primary_icon_name="system-search-symbolic",
            secondary_icon_name="edit-clear-symbolic",
            height_request=48,
            hexpand=True,
        )
        self.search_entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
        self.search_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Clear search")
        self.search_entry.connect("icon-press", self._clear_search)
        search = Gtk.Box(spacing=8)
        search.append(self.search_entry)
        self.load_button = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Load unpacked extension",
            width_request=48,
            height_request=48,
            valign=Gtk.Align.CENTER,
        )
        self.load_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Load unpacked extension"]
        )
        self.load_button.connect("clicked", self._choose_folder)
        search.append(self.load_button)
        body.append(search)
        body.append(self.sections)
        installed = self._section_body()
        installed.append(
            Gtk.Label(
                label="Load an unpacked Manifest V3 folder with +. Keep its files in place; "
                "changes apply after restarting Mini Browser.",
                wrap=True,
                xalign=0,
                css_classes=["caption", "dim-label"],
            )
        )
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False, css_classes=["error"])
        installed.append(self.status)
        self.restart_notice = Gtk.Label(
            label="Restart Mini Browser to apply extension changes.",
            wrap=True,
            xalign=0,
            visible=False,
            css_classes=["heading"],
        )
        installed.append(self.restart_notice)
        self.empty = Gtk.Label(
            label="No extensions installed", css_classes=["dim-label"], wrap=True
        )
        installed.append(self.empty)
        self.installed_grid = self._tile_grid()
        installed.append(self.installed_grid)
        self.sections.add_titled(self._scroll(installed), "installed", "Installed")
        market = self._section_body()
        market.append(
            Gtk.Label(
                label="Install extensions from the Chrome Web Store. Changes apply after "
                "restarting Mini Browser. Compatibility with Mini Browser is not yet verified.",
                wrap=True,
                xalign=0,
                css_classes=["caption", "dim-label"],
            )
        )
        self.market_empty = Gtk.Label(label="No matching extensions", css_classes=["dim-label"])
        market.append(self.market_empty)
        self.market_grid = self._tile_grid()
        market.append(self.market_grid)
        self.sections.add_titled(self._scroll(market), "market", "Market")
        self.sections.connect("notify::visible-child-name", self._section_changed)
        self.search_entry.connect("changed", lambda *_: self.refresh())
        self._section_changed()
        self.refresh()

    @staticmethod
    def _section_body():
        return Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=8, margin_bottom=24
        )

    @staticmethod
    def _scroll(child):
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=child)

    @staticmethod
    def _tile_grid():
        return Gtk.FlowBox(
            css_classes=["recent-sites"],
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=6,
            column_spacing=12,
            row_spacing=12,
        )

    def _section_changed(self, *_):
        self.load_button.set_visible(self.sections.get_visible_child_name() == "installed")

    def _clear_search(self, _entry, position):
        if position == Gtk.EntryIconPosition.SECONDARY:
            self.search_entry.set_text("")

    @staticmethod
    def _card(title, subtitle):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        body.append(Adw.Avatar(size=44, text=title, show_initials=True, halign=Gtk.Align.CENTER))
        body.append(
            Gtk.Label(
                label=title,
                lines=2,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=12,
                width_chars=10,
                justify=Gtk.Justification.CENTER,
                css_classes=["heading"],
            )
        )
        body.append(
            Gtk.Label(
                label=subtitle,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=14,
                css_classes=["caption", "dim-label"],
            )
        )
        card.append(body)
        return card, body

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

    def _refresh_managers(self):
        for window in self.app.get_windows():
            if not getattr(window, "_closing", True) and hasattr(window, "extensions_page"):
                window.extensions_page.refresh()

    def _install_market(self, _button, entry):
        identifier = entry["id"]
        if self.app.extension_install_state.get(identifier, {}).get("busy"):
            return
        state = {"busy": True, "message": "Downloading…", "error": False}
        self.app.extension_install_state[identifier] = state
        self.app.hold()
        self._refresh_managers()

        def progress(message):
            if not self.app._shutting_down:
                state["message"] = message
                self._refresh_managers()
            return GLib.SOURCE_REMOVE

        def finished(path, message):
            state.update(busy=False, message=message, error=bool(message))
            try:
                if not self.app._shutting_down:
                    if path:
                        self._saved()
                    else:
                        self._refresh_managers()
            finally:
                self.app.release()
            return GLib.SOURCE_REMOVE

        def work():
            path = message = None
            try:
                path = self.app.extension_installer.install(
                    entry,
                    progress=lambda text: GLib.idle_add(progress, text),
                    cancelled=lambda: self.app._shutting_down,
                )
            except ValueError as error:
                message = str(error)
            except (OSError, DatabaseError):
                message = "Could not install the extension. Check available space and try again."
            GLib.idle_add(finished, path, message)

        Thread(target=work, daemon=True, name="market-extension-install").start()

    def _show_installed(self, _button):
        self.search_entry.set_text("")
        self.sections.set_visible_child_name("installed")

    def refresh(self):
        self.installed_grid.remove_all()
        records = self.app.extensions.all()
        query = self.search_entry.get_text().strip().casefold()
        visible = [
            record
            for record in records
            if query in " ".join((record["name"], record["version"], record["path"])).casefold()
        ]
        self.empty.set_label("No matching extensions" if records else "No extensions installed")
        self.empty.set_visible(not visible)
        self.installed_grid.set_visible(bool(visible))
        self.restart_notice.set_visible(self.app.extensions_changed)
        for record in visible:
            self.installed_grid.append(self._installed_card(record))
        self.market_grid.remove_all()
        market = [
            record for record in MARKET_EXTENSIONS if query in " ".join(record.values()).casefold()
        ]
        self.market_empty.set_visible(not market)
        self.market_grid.set_visible(bool(market))
        installed = self.app.extensions.market_installs()
        paths = {item["path"] for item in records}
        for path, launcher in self.app.extension_launchers.items():
            if path in paths:
                installed.setdefault(launcher["id"], path)
        for record in market:
            self.market_grid.append(self._market_card(record, record["id"] in installed))

    def _installed_card(self, record):
        card, body = self._card(record["name"], f"Version {record['version']}")
        card.set_tooltip_text(f"{record['name']}\n{record['path']}")
        body.append(
            Gtk.Label(
                label="Enabled" if record["enabled"] else "Disabled",
                css_classes=["caption", "dim-label"],
            )
        )
        error = self.app.extension_errors.get(record["path"])
        if error:
            body.append(
                Gtk.Label(
                    label=error,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    lines=3,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=14,
                    css_classes=["caption", "error"],
                )
            )
        launcher = self.app.extension_launchers.get(record["path"])
        if launcher is None or launcher["popup"]:
            open_popup = Gtk.Button(
                label="Open popup",
                tooltip_text="Open popup in its own window"
                if launcher
                else "Restart Mini Browser to open this extension's popup",
                sensitive=bool(launcher and launcher["popup"] and record["enabled"]),
            )
            open_popup.connect("clicked", self._open_popup, record["path"])
            body.append(open_popup)
        else:
            body.append(
                Gtk.Label(label="No popup", height_request=34, css_classes=["caption", "dim-label"])
            )
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        switch = Gtk.Switch(
            active=bool(record["enabled"]),
            valign=Gtk.Align.CENTER,
            tooltip_text="Enable extension after restart",
        )
        switch.update_property([Gtk.AccessibleProperty.LABEL], [f"Enable {record['name']}"])
        switch.connect("notify::active", self._toggle, record["path"])
        controls.append(switch)
        remove = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text=f"Remove {record['name']}",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        remove.connect(
            "clicked",
            lambda _button: self._change(lambda: self.app.extensions.remove(record["path"])),
        )
        controls.append(remove)
        body.append(controls)
        return card

    def _market_card(self, record, installed):
        card, body = self._card(record["name"], record["category"])
        card.set_tooltip_text(f"{record['name']}\n{record['publisher']}\n{record['url']}")
        for text, classes, lines in (
            (record["publisher"], ["caption", "dim-label"], 2),
            (record["description"], ["caption"], 3),
            ("Not verified", ["caption", "dim-label"], 1),
        ):
            body.append(
                Gtk.Label(
                    label=text,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    lines=lines,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=14,
                    width_chars=10,
                    justify=Gtk.Justification.CENTER,
                    css_classes=classes,
                )
            )
        state = self.app.extension_install_state.get(record["id"], {})
        label = "Installed" if installed else "Installing…" if state.get("busy") else "Install"
        install = Gtk.Button(
            label=label,
            sensitive=not state.get("busy", False),
            css_classes=[] if installed else ["suggested-action"],
        )
        install.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} {record['name']}"])
        if installed:
            install.connect("clicked", self._show_installed)
        else:
            install.connect("clicked", self._install_market, record)
        body.append(install)
        if state.get("message"):
            status = Gtk.Label(
                label=state["message"],
                wrap=True,
                lines=3,
                max_width_chars=14,
                ellipsize=Pango.EllipsizeMode.END,
                tooltip_text=state["message"],
                justify=Gtk.Justification.CENTER,
                css_classes=["caption", "error" if state.get("error") else "dim-label"],
            )
            body.append(status)
        official = Gtk.Button(
            label="Official page", tooltip_text=record["url"], css_classes=["flat"]
        )
        official.connect("clicked", lambda _button: self.app.new_window(record["url"]))
        body.append(official)
        return card
