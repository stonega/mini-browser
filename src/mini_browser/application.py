"""GNOME application lifecycle, shared web session, and desktop actions."""

import os
import sys
from pathlib import Path

from . import APP_ID
from .gtk import Adw, Gdk, Gio, GLib, Gtk, WebKit
from .navigation import address_to_uri
from .storage import Library
from .vault import PasswordVault
from .window import BrowserWindow


class BrowserApplication(Adw.Application):
    def __init__(self, *, data_dir=None, cache_dir=None, application_id=APP_ID):
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.data_dir = Path(
            data_dir
            or Path(
                os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"),
            )
            / "mini-browser"
        )
        self.cache_dir = Path(
            cache_dir
            or Path(
                os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"),
            )
            / "mini-browser"
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        # The card buttons own interaction styling; FlowBox only arranges them.
        styles = Gtk.CssProvider()
        styles.load_from_string("""
            flowbox.recent-sites > flowboxchild {
                background: none;
                box-shadow: none;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), styles, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        self.cache_dir.chmod(0o700)
        self.library = Library(self.data_dir / "library.sqlite3")
        self.vault = PasswordVault(self.data_dir / "passwords.vault")
        self.vault_requests = set()
        self._vault_timer = GLib.timeout_add_seconds(2, self._expire_vault)
        self._lock_subscriptions = []
        self._watch_session_lock()
        self.connect("window-removed", lambda *_: self.get_windows() or self.lock_vault())
        self.network_session = WebKit.NetworkSession.new(
            str(self.data_dir / "webkit"),
            str(self.cache_dir / "webkit"),
        )
        self.network_session.set_persistent_credential_storage_enabled(False)
        self.network_session.get_cookie_manager().set_persistent_storage(
            str(self.data_dir / "cookies.sqlite3"),
            WebKit.CookiePersistentStorage.SQLITE,
        )
        self.network_session.connect("download-started", self._download_started)
        for name, callback in [("new-window", self.new_window), ("quit", self.quit)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        shortcuts = {
            "app.new-window": ["<Primary>n"],
            "app.quit": ["<Primary>q"],
            "win.address": ["<Primary>l"],
            "win.home": ["<Alt>Home"],
            "win.settings": ["<Primary>comma"],
            "win.show-header": ["F10"],
            "win.close": ["<Primary>w"],
            "win.reload": ["<Primary>r", "F5"],
            "win.back": ["<Alt>Left"],
            "win.forward": ["<Alt>Right"],
            "win.stop": ["Escape"],
            "win.import": ["<Primary><Shift>i"],
            "win.library": ["<Primary>b", "<Primary>h"],
            "win.bookmark": ["<Primary>d"],
            "win.passwords": ["<Primary><Shift>p"],
            "win.fill-login": ["<Primary><Shift>l"],
            "win.lock-vault": ["<Primary><Shift>k"],
            "win.shortcuts": ["<Primary>question", "F1"],
            "win.zoom-in": ["<Primary>plus", "<Primary>equal"],
            "win.zoom-out": ["<Primary>minus"],
            "win.zoom-reset": ["<Primary>0"],
        }
        for action, accelerators in shortcuts.items():
            self.set_accels_for_action(action, accelerators)

    def lock_vault(self):
        self.vault.lock()
        for request in tuple(self.vault_requests):
            request.cancel()
        for window in self.get_windows():
            # Close stacked password dialogs, clearing their native credential rows.
            for dialog in reversed(list(window.get_dialogs())):
                if getattr(dialog, "_password_dialog", False):
                    dialog.force_close()

    def _expire_vault(self):
        if self.vault.expire():
            self.lock_vault()
        return GLib.SOURCE_CONTINUE

    def _watch_session_lock(self):
        def changed(_bus, _sender, _path, _interface, _signal, parameters, _data=None):
            if parameters.unpack()[0]:
                self.lock_vault()

        for bus_type, sender, interface, signal, path in [
            (
                Gio.BusType.SESSION,
                "org.gnome.ScreenSaver",
                "org.gnome.ScreenSaver",
                "ActiveChanged",
                "/org/gnome/ScreenSaver",
            ),
            (
                Gio.BusType.SYSTEM,
                "org.freedesktop.login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                "/org/freedesktop/login1",
            ),
        ]:
            try:
                connection = Gio.bus_get_sync(bus_type, None)
                subscription = connection.signal_subscribe(
                    sender,
                    interface,
                    signal,
                    path,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    changed,
                )
                self._lock_subscriptions.append((connection, subscription))
            except GLib.Error:
                pass  # Manual locking and the timeout work without session services.

    def do_shutdown(self):
        self.lock_vault()
        GLib.source_remove(self._vault_timer)
        for connection, subscription in self._lock_subscriptions:
            connection.signal_unsubscribe(subscription)
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        self.new_window()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if args and args[0] == "--":
            args = args[1:]
        if not args:
            self.activate()
            return 0
        uris = []
        for address in args:
            try:
                uris.append(address_to_uri(address))
            except ValueError as exc:
                command_line.printerr(str(exc) + "\n")
                return 2
        for uri in uris:
            self.new_window(uri)
        return 0

    def new_window(self, uri=None):
        window = BrowserWindow(self)
        if uri:
            window.load_uri(uri)
        window.present()
        return window

    def _download_started(self, _session, download):
        """Choose a destination for every download; never silently overwrite a file."""
        window = self.get_active_window()
        self.hold()
        finished = False

        def release(*_):
            nonlocal finished
            if not finished:
                finished = True
                self.release()

        download.connect("finished", release)
        download.connect("failed", release)

        def decide_destination(_download, suggested):
            # Suggested names come from the web; restrict them to a single filename.
            picker = Gtk.FileDialog(title="Save download", initial_name=Path(suggested).name)

            def selected(picker, result):
                try:
                    destination = picker.save_finish(result)
                    if not destination.get_path():
                        download.cancel()
                        return
                    download.set_allow_overwrite(True)  # Native picker confirms replacement.
                    download.set_destination(destination.get_uri())
                except Exception:
                    download.cancel()

            picker.save(window, None, selected)
            return True

        download.connect("decide-destination", decide_destination)
        download.connect("finished", lambda *_: window and window.toast("Download saved"))


def main(argv=None):
    return BrowserApplication().run(argv or sys.argv)
