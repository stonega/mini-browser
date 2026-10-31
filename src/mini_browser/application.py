"""GNOME application lifecycle, shared web session, and desktop actions."""

import os
import shutil
import sys
import time
import weakref
from pathlib import Path
from urllib.parse import urlsplit

from . import APP_ID
from .agent_activity import AgentActivity
from .deepseek import DeepSeekHarness
from .device_discovery import AndroidDiscovery, device_tiles
from .devices import DeviceStore
from .extension_page import ExtensionsWindow
from .extension_popup import ExtensionPopupWindow
from .extensions import ExtensionStore, extension_ui
from .gtk import Adw, Gdk, Gio, GLib, Gtk, Web
from .navigation import address_to_uri
from .script_apps import ScriptAppHarness, ScriptAppStore
from .storage import Library
from .vault import PasswordVault
from .window import BrowserWindow


class BrowserApplication(Adw.Application):
    def __init__(
        self,
        *,
        data_dir=None,
        cache_dir=None,
        application_id=APP_ID,
        discover_devices=True,
        allow_autoplay=False,
        silent=False,
    ):
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.allow_autoplay, self.silent = allow_autoplay, silent
        self._discover_devices = discover_devices
        self._shutting_down = False
        self.discovery = None
        self.agent_control = None
        self.extensions_window = None
        self.extension_popup_windows = {}
        self.agent_activity = AgentActivity(self._agent_activity_changed)
        self.add_main_option(
            "agent",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Enable private local agent control",
            None,
        )
        self.deepseek = DeepSeekHarness()
        self.script_runners = {}
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
        # Resolve the About icon even without a desktop icon installation.
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).resolve().parent / "icons")
        )
        # The card buttons own interaction styling; FlowBox only arranges them.
        styles = Gtk.CssProvider()
        styles.load_from_string("""
            flowbox.recent-sites > flowboxchild {
                background: none;
                box-shadow: none;
            }
            .phone-screen { background: black; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), styles, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        self.cache_dir.chmod(0o700)
        self.library = Library(self.data_dir / "library.sqlite3")
        self.script_apps = ScriptAppStore(self.library)
        self.extensions = ExtensionStore(self.library)
        extension_paths, self.extension_errors = self.extensions.startup()
        self.extension_launchers = {}
        for path in extension_paths:
            try:
                self.extension_launchers[path] = extension_ui(path)
            except ValueError as error:
                self.extension_errors[path] = str(error)
        self.extensions_changed = False
        self.devices = DeviceStore(self.library)
        self.connected_android = ()
        self.discovery_error = None
        self.device_windows = {}
        self.device_transports = set()
        self.removed_devices = set()
        self.device_dialogs = weakref.WeakSet()
        self.vault = PasswordVault(self.data_dir / "passwords.vault")
        self.vault_requests = set()
        self._vault_timer = GLib.timeout_add_seconds(2, self._expire_vault)
        self._lock_subscriptions = []
        self._watch_session_lock()
        self.connect("window-removed", lambda *_: self.get_windows() or self.lock_vault())
        self.network_session = Web.Session(
            self.data_dir / "chromium",
            self.cache_dir / "chromium",
            allow_autoplay=self.allow_autoplay,
            silent=self.silent,
            extensions=extension_paths,
        )
        self.network_session.connect("download-started", self._download_started)
        self.network_session.connect(
            "extension-window", lambda _session, uri: self.open_extension_uri(uri)
        )
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
            "win.reload-bypass-cache": ["<Primary><Shift>r", "<Primary>F5"],
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
        if self._discover_devices:
            self.discovery = AndroidDiscovery(self._devices_detected)
            self.discovery.start()

    def _agent_activity_changed(self):
        for window in self.get_windows():
            if isinstance(window, BrowserWindow) and not window._closing:
                window.refresh_agent_actions()

    def _devices_detected(self, records, error):
        def update():
            if not self._shutting_down:
                self.devices.bind_android_identities(records)
                self.connected_android = records
                self.discovery_error = error
                self.refresh_devices()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(update)

    def device_tiles(self):
        return device_tiles(self.devices.all(), self.connected_android)

    def android_identity(self, address):
        return next(
            (item.identity for item in self.connected_android if item.address == address), ""
        )

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
                self.disconnect_devices()

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
        self._shutting_down = True
        if self.agent_control:
            self.agent_control.close()
        for window in self.get_windows():
            if hasattr(window, "window_size"):
                window.window_size.close()
        self.deepseek.close()
        for runner in self.script_runners.values():
            runner.close()
        if self.discovery:
            self.discovery.stop()
            self.discovery.thread.join(timeout=3)
        self.disconnect_devices()
        deadline = time.monotonic() + 10
        for transport in tuple(self.device_transports):
            transport.stop()
            if transport.thread:
                transport.thread.join(timeout=max(0, deadline - time.monotonic()))
        self.lock_vault()
        GLib.source_remove(self._vault_timer)
        for connection, subscription in self._lock_subscriptions:
            connection.signal_unsubscribe(subscription)
        self.network_session.close()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        self.new_window()

    def do_command_line(self, command_line):
        if command_line.get_options_dict().contains("agent") and self.agent_control is None:
            from .automation import Automation
            from .control import ControlError, ControlServer

            try:
                automation = Automation(self)
                self.agent_control = ControlServer(automation.dispatch)
            except (ControlError, OSError) as error:
                command_line.printerr(f"Cannot enable agent control: {error}\n")
                return 1
            self.connect("window-removed", automation.window_removed)
            self.hold()
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

    def open_extensions(self):
        if self.extensions_window is None:
            self.extensions_window = ExtensionsWindow(self)
        self.extensions_window.present()
        return self.extensions_window

    def open_extension_popup(self, path):
        extension = self.extension_launchers.get(path)
        if not extension:
            raise ValueError("Enable this extension and restart Mini Browser to open its popup.")
        if not extension["popup"]:
            raise ValueError("This extension does not declare a popup.")
        window = self.open_extension_uri(extension["popup"])
        if window is None:
            raise ValueError("Enable this extension and restart Mini Browser to open its popup.")
        return window

    def open_extension_uri(self, uri):
        if self._shutting_down:
            return None
        parts = urlsplit(uri)
        origin = f"{parts.scheme}://{parts.netloc}"
        enabled = {record["path"] for record in self.extensions.all() if record["enabled"]}
        extension = next(
            (
                item
                for path, item in self.extension_launchers.items()
                if path in enabled and item["origin"] == origin
            ),
            None,
        )
        if extension is None:
            return None
        window = self.extension_popup_windows.get(uri)
        if window is None:
            window = ExtensionPopupWindow(self, extension, uri)
            self.extension_popup_windows[uri] = window
        window.present()
        return window

    def script_runner(self, record):
        if record["id"] not in self.script_runners:
            self.script_runners[record["id"]] = ScriptAppHarness(record)
        return self.script_runners[record["id"]]

    def owns_script_uri(self, uri):
        return self.deepseek.owns_uri(uri) or any(
            runner.owns_uri(uri) for runner in self.script_runners.values()
        )

    def return_home(self, source):
        if source._closing:
            return None
        if isinstance(source, BrowserWindow) and source.stack.get_visible_child_name() == "home":
            source.present()
            return source
        home = next(
            (
                window
                for window in self.get_windows()
                if isinstance(window, BrowserWindow)
                and not window._closing
                and window.stack.get_visible_child_name() == "home"
            ),
            None,
        )
        # Keep a Home window alive before closing the application's last app.
        if home is None:
            home = self.new_window()
        else:
            home.present()
        if source is not home:
            source.close()
        return home

    def open_device(self, device_id):
        from .device_window import DeviceWindow

        device = next((item for item, _state in self.device_tiles() if item.id == device_id), None)
        if device is None:
            return None
        window = self.device_windows.get(device.id)
        if window is None:
            window = DeviceWindow(self, device)
            self.device_windows[device.id] = window
        window.present()
        return window

    def refresh_devices(self):
        records = {device.id: device for device, _state in self.device_tiles()}
        for device_id, window in tuple(self.device_windows.items()):
            if device_id in records:
                window.device = records[device_id]
                window.set_title(window.device.name)
                window.title_widget.set_title(window.device.name)
        for window in self.get_windows():
            if isinstance(window, BrowserWindow):
                window.refresh_recents()
        for dialog in tuple(self.device_dialogs):
            dialog.refresh()

    def remove_device(self, device_id):
        saved = {device.id: device for device in self.devices.all()}
        if device_id not in saved:
            return
        selected = saved[device_id]
        aliases = [
            item.id
            for item in saved.values()
            if item.id == device_id
            or (
                selected.kind == item.kind == "android"
                and selected.identity
                and item.identity == selected.identity
            )
        ]
        for alias in aliases:
            self.devices.remove(alias)
            self.removed_devices.add(alias)
            window = self.device_windows.get(alias)
            if window:
                window.close()
            self._remove_pairing_data(alias)
        self.refresh_devices()

    def finish_device_transport(self, transport):
        self.device_transports.discard(transport)
        try:
            if transport.device.id in self.removed_devices:
                self._remove_pairing_data(transport.device.id)
        except OSError:
            window = self.get_active_window()
            if window:
                window.toast("Phone removed, but local pairing data could not be deleted.")
        finally:
            self.release()
        return GLib.SOURCE_REMOVE

    def _remove_pairing_data(self, device_id):
        if any(transport.device.id == device_id for transport in self.device_transports):
            return
        # IDs come from the registry, never from an address or phone-supplied path.
        if len(device_id) != 32 or any(char not in "0123456789abcdef" for char in device_id):
            return
        directory = self.data_dir / "devices" / device_id
        if directory.is_dir():
            shutil.rmtree(directory)
        self.removed_devices.discard(device_id)

    def disconnect_devices(self):
        for window in tuple(self.device_windows.values()):
            window.disconnect("Session locked or stopped. Select Reconnect to resume.")
        for transport in tuple(self.device_transports):
            transport.stop()

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
