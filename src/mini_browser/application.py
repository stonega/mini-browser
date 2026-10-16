"""GNOME application lifecycle, shared web session, and desktop actions."""

import os
import sys
from pathlib import Path

from . import APP_ID
from .gtk import Adw, Gdk, Gio, Gtk, WebKit
from .navigation import address_to_uri
from .storage import Library
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
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        self.cache_dir.chmod(0o700)
        self.library = Library(self.data_dir / "library.sqlite3")
        self.network_session = WebKit.NetworkSession.new(
            str(self.data_dir / "webkit"),
            str(self.cache_dir / "webkit"),
        )
        self.network_session.get_cookie_manager().set_persistent_storage(
            str(self.data_dir / "cookies.sqlite3"),
            WebKit.CookiePersistentStorage.SQLITE,
        )
        self.network_session.connect("download-started", self._download_started)
        css = Gtk.CssProvider()
        css.load_from_string("""
            .mini-close {
                color: #fff;
                background: rgba(30, 30, 30, 0.78);
                box-shadow: 0 1px 5px rgba(0, 0, 0, 0.18);
                padding: 5px;
                min-width: 24px;
                min-height: 24px;
            }
            .mini-close:hover { background: rgba(55, 55, 55, 0.96); }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        for name, callback in [("new-window", self.new_window), ("quit", self.quit)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        shortcuts = {
            "app.new-window": ["<Primary>n"],
            "app.quit": ["<Primary>q"],
            "win.address": ["<Primary>l"],
            "win.close": ["<Primary>w"],
            "win.reload": ["<Primary>r", "F5"],
            "win.back": ["<Alt>Left"],
            "win.forward": ["<Alt>Right"],
            "win.stop": ["Escape"],
            "win.import": ["<Primary><Shift>i"],
            "win.library": ["<Primary>b", "<Primary>h"],
            "win.bookmark": ["<Primary>d"],
            "win.shortcuts": ["<Primary>question", "F1"],
            "win.zoom-in": ["<Primary>plus", "<Primary>equal"],
            "win.zoom-out": ["<Primary>minus"],
            "win.zoom-reset": ["<Primary>0"],
        }
        for action, accelerators in shortcuts.items():
            self.set_accels_for_action(action, accelerators)

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

    def new_window(self, uri=None, *, related_view=None):
        window = BrowserWindow(self, related_view=related_view)
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
