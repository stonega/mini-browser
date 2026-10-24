"""Phone setup and management stay in native Settings dialogs."""

import sqlite3
import threading

from .devices import KINDS, DeviceError, discover_android
from .gtk import Adw, GLib, Gtk


def devices_dialog(window):
    return DevicesDialog(window)


class DevicesDialog(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__(
            title="Devices", content_width=400, content_height=600, search_enabled=False
        )
        self.window = window
        self.app = window.get_application()
        page = Adw.PreferencesPage()
        self.saved = Adw.PreferencesGroup(title="Saved phones")
        self.rows = []
        page.add(self.saved)
        add = Adw.PreferencesGroup(title="Add a phone")
        for kind, subtitle in (
            ("android", "USB or an already paired ADB connection · mouse and keyboard"),
            ("iphone", "AirPlay on your local network · screen viewing"),
        ):
            row = Adw.ActionRow(title=f"Add {KINDS[kind]}", subtitle=subtitle, activatable=True)
            row.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
            row.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
            row.connect("activated", lambda _row, k=kind: DeviceEditor(self, k))
            add.add(row)
        page.add(add)
        self.add(page)
        self.app.device_dialogs.add(self)
        self.connect("closed", lambda *_: self.app.device_dialogs.discard(self))
        self.refresh()
        self.present(window)

    def refresh(self):
        for row in self.rows:
            self.saved.remove(row)
        self.rows.clear()
        for device in self.app.devices.all():
            row = Adw.ActionRow(
                title=device.name,
                subtitle=KINDS[device.kind],
                use_markup=False,
            )
            row.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
            for icon, tooltip, callback in (
                (
                    "media-playback-start-symbolic",
                    "Open phone",
                    lambda d=device: self.open_phone(d),
                ),
                (
                    "document-edit-symbolic",
                    "Rename phone",
                    lambda d=device: DeviceEditor(self, d.kind, d),
                ),
                ("user-trash-symbolic", "Remove phone", lambda d=device: self.remove_phone(d)),
            ):
                button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
                button.add_css_class("flat")
                button.connect("clicked", lambda _button, cb=callback: cb())
                row.add_suffix(button)
            self.rows.append(row)
            self.saved.add(row)
        self.saved.set_description(
            None if self.rows else "Add a phone to show it as an app on Home."
        )

    def open_phone(self, device):
        self.force_close()
        self.app.open_device(device.id)

    def remove_phone(self, device):
        confirmation = Adw.AlertDialog(
            heading="Remove phone?",
            body="This removes the app tile and its local pairing data. Your phone's data is kept.",
        )
        confirmation.add_response("cancel", "Cancel")
        confirmation.add_response("remove", "Remove")
        confirmation.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        confirmation.set_default_response("cancel")
        confirmation.set_close_response("cancel")

        def response(_dialog, result):
            if result == "remove":
                try:
                    self.app.remove_device(device.id)
                except (OSError, sqlite3.Error):
                    self.window.toast("Could not remove phone. Check disk space and try again.")

        confirmation.connect("response", response)
        confirmation.present(self.window)


class DeviceEditor(Adw.PreferencesDialog):
    def __init__(self, parent, kind, device=None):
        super().__init__(
            title="Rename phone" if device else f"Add {KINDS[kind]}",
            content_width=400,
            content_height=520,
            search_enabled=False,
        )
        self.parent, self.kind, self.device = parent, kind, device
        self.alive = True
        self.connect("closed", self._closed)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title="Phone name", text=device.name if device else KINDS[kind])
        group.add(self.name)
        self.address = Adw.EntryRow(
            title="Device serial or paired ADB address",
            text=device.address if device else "",
        )
        if kind == "android" and device is None:
            group.set_description(
                "Enable USB debugging in Android Developer options, connect by USB, and "
                "approve this computer on the phone. Wireless ADB must already be paired."
            )
            group.add(self.address)
            self.find_button = Gtk.Button(label="Find connected phones", margin_top=12)
            self.find_button.connect("clicked", self.find_phones)
            group.add(self.find_button)
            self.choices = Adw.PreferencesGroup(title="Connected phones")
        elif kind == "iphone" and device is None:
            group.set_description(
                "Keep your computer and iPhone on the same local network. Open the saved tile, "
                "then choose its receiver in iPhone Control Center → Screen Mirroring. "
                "Keep the iPhone unlocked; control it on the phone."
            )
        page.add(group)
        if kind == "android" and device is None:
            page.add(self.choices)
        actions = Adw.PreferencesGroup()
        self.error = Gtk.Label(wrap=True, xalign=0, css_classes=["error"], visible=False)
        actions.add(self.error)
        self.save_button = Gtk.Button(
            label="Save" if device else "Add phone",
            css_classes=["suggested-action"],
            margin_top=12,
        )
        self.save_button.connect("clicked", self.save)
        actions.add(self.save_button)
        page.add(actions)
        self.add(page)
        self.present(parent.window)

    def _closed(self, *_):
        self.alive = False

    def save(self, *_):
        try:
            self.parent.app.devices.save(
                self.kind,
                self.name.get_text(),
                self.address.get_text(),
                device_id=self.device.id if self.device else None,
            )
            self.parent.app.refresh_devices()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error.set_label(
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not save phone. Check storage space and permissions, then try again."
            )
            self.error.set_visible(True)
            return
        self.close()

    def find_phones(self, *_):
        self.find_button.set_sensitive(False)
        self.error.set_visible(False)
        self.choices.set_description("Finding phones…")

        def complete(records, error):
            if not self.alive:
                return GLib.SOURCE_REMOVE
            self.find_button.set_sensitive(True)
            for row in getattr(self, "choice_rows", []):
                self.choices.remove(row)
            self.choice_rows = []
            self.choices.set_description(error or (None if records else "No Android phones found."))
            for address, model, state in records:
                row = Adw.ActionRow(
                    title=model,
                    subtitle=f"{address} · {state}",
                    use_markup=False,
                    activatable=True,
                )

                def select(_row, serial=address, label=model):
                    self.address.set_text(serial)
                    self.name.set_text(label)

                row.connect("activated", select)
                self.choices.add(row)
                self.choice_rows.append(row)
            return GLib.SOURCE_REMOVE

        def worker():
            try:
                records, error = discover_android(), None
            except DeviceError as exc:
                records, error = [], str(exc)
            GLib.idle_add(complete, records, error)

        threading.Thread(target=worker, name="phone-discovery", daemon=True).start()
