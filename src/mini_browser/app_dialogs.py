"""Native forms for saving website and command-backed Home apps."""

import sqlite3
from pathlib import Path

from .dialogs import padded_box
from .gtk import Adw, Gtk
from .navigation import is_web_url


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
