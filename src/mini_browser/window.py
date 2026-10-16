"""One WebView per portrait window with a native GNOME header bar."""

from . import APP_NAME, autofill
from .dialogs import (
    address_dialog,
    import_dialog,
    library_dialog,
    settings_dialog,
    shortcuts_dialog,
)
from .gtk import Adw, Gio, Gtk, Pango, WebKit
from .navigation import address_to_uri, is_web_url
from .password_dialogs import fill_login, passwords_dialog

DEFAULT_WIDTH = 420
DEFAULT_HEIGHT = 780


class BrowserWindow(Adw.ApplicationWindow):
    def __init__(self, app, *, related_view=None):
        super().__init__(
            application=app,
            title=APP_NAME,
            default_width=DEFAULT_WIDTH,
            default_height=DEFAULT_HEIGHT,
        )
        self.library = app.library
        self.failed = False
        self.failed_uri = None
        self._closing = False
        self.document_generation = 0
        if related_view:
            self.webview = WebKit.WebView(related_view=related_view)
        else:
            self.webview = WebKit.WebView(network_session=app.network_session)
        self._user_content_manager = self.webview.get_user_content_manager()
        if not getattr(self._user_content_manager, "_mini_vault_world", False):
            # Register and retain the isolated world across asynchronous API calls.
            script = WebKit.UserScript.new_for_world(
                autofill.INITIALIZE,
                WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.START,
                autofill.WORLD,
                None,
                None,
            )
            self._user_content_manager.add_script(script)
            self._user_content_manager._mini_vault_world = True
        settings = self.webview.get_settings()
        settings.set_property("enable-developer-extras", False)
        settings.set_property("media-playback-requires-user-gesture", True)
        self.webview.connect("create", self._create_webview)
        self.webview.connect("close", lambda *_: self.close())
        self.webview.connect("load-changed", self._load_changed)
        self.webview.connect("load-failed", self._load_failed)
        self.webview.connect("load-failed-with-tls-errors", self._tls_failed)
        self.webview.connect("notify::title", self._title_changed)
        self.webview.connect("decide-policy", self._decide_policy)
        self.webview.connect("web-process-terminated", self._web_process_terminated)

        self.stack = Gtk.Stack()
        self.stack.add_named(self._home(), "home")
        self.stack.add_named(self.webview, "web")
        self.error_page = Adw.StatusPage(
            icon_name="network-error-symbolic",
            title="Could not load this page",
        )
        retry = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        retry.add_css_class("suggested-action")
        retry.connect("clicked", self._retry)
        error_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        error_actions.append(retry)
        open_button = Gtk.Button(label="Open another website", css_classes=["flat"])
        open_button.connect("clicked", lambda *_: address_dialog(self))
        error_actions.append(open_button)
        self.error_page.set_child(error_actions)
        self.stack.add_named(self.error_page, "error")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.headerbar = Adw.HeaderBar(decoration_layout=":close")
        self.headerbar.pack_end(
            Gtk.Button(
                icon_name="emblem-system-symbolic",
                tooltip_text="Settings (Ctrl+,)",
                action_name="win.settings",
            )
        )
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(self.headerbar)
        self.set_content(toolbar)
        self._add_actions()
        self.set_focus(self.home_entry)
        self.connect("close-request", self._close_requested)

    def _home(self):
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=32,
            margin_top=40,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        self.home_entry = Gtk.Entry(
            placeholder_text="Search or enter an address",
            primary_icon_name="system-search-symbolic",
            secondary_icon_name="go-next-symbolic",
            height_request=48,
        )
        self.home_entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
        self.home_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Open website")
        content.append(self.home_entry)

        def open_address(*_):
            try:
                self.load_uri(address_to_uri(self.home_entry.get_text()))
            except ValueError as exc:
                self.toast(str(exc))

        self.home_entry.connect("activate", open_address)
        self.home_entry.connect(
            "icon-press",
            lambda _entry, position: (
                open_address() if position == Gtk.EntryIconPosition.SECONDARY else None
            ),
        )
        recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        recent.append(Gtk.Label(label="Recently opened", xalign=0, css_classes=["heading"]))
        self.recent_tiles = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=3,
            column_spacing=12,
            row_spacing=12,
        )
        self.recent_empty = Gtk.Label(
            label="Sites you open will appear here.",
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        recent.append(self.recent_tiles)
        recent.append(self.recent_empty)
        content.append(recent)
        self.refresh_recents()
        clamp = Adw.Clamp(maximum_size=420, tightening_threshold=360, child=content)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)

    def refresh_recents(self):
        self.recent_tiles.remove_all()
        records = self.library.recent_apps()
        self.recent_empty.set_visible(not records)
        self.recent_tiles.set_visible(bool(records))
        for record in records:
            host = record["site"].split("://", 1)[1].removeprefix("www.")
            title = " ".join((record["title"] or host).split())
            body = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
                margin_top=12,
                margin_bottom=12,
                margin_start=4,
                margin_end=4,
                halign=Gtk.Align.CENTER,
            )
            body.append(Adw.Avatar(size=44, text=host, show_initials=True))
            body.append(
                Gtk.Label(
                    label=title,
                    lines=2,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=10,
                    width_chars=8,
                    justify=Gtk.Justification.CENTER,
                    css_classes=["heading"],
                )
            )
            body.append(
                Gtk.Label(
                    label=host,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=12,
                    css_classes=["caption", "dim-label"],
                )
            )
            button = Gtk.Button(
                child=body,
                css_classes=["flat", "card"],
                tooltip_text=f"{title}\n{record['site']}",
            )
            button.connect("clicked", lambda _button, url=record["url"]: self.load_uri(url))
            self.recent_tiles.append(button)

    def show_home(self):
        self.webview.stop_loading()
        self.refresh_recents()
        self.stack.set_visible_child_name("home")
        self.set_title(APP_NAME)
        self.home_entry.set_text("")
        self.home_entry.grab_focus()

    def _add_actions(self):
        for name, callback in {
            "address": lambda: address_dialog(self),
            "home": self.show_home,
            "settings": lambda: settings_dialog(self),
            "close": self.close,
            "reload": self._retry,
            "back": self.webview.go_back,
            "forward": self.webview.go_forward,
            "stop": self.webview.stop_loading,
            "import": lambda: import_dialog(self),
            "library": lambda: library_dialog(self),
            "bookmark": self._bookmark,
            "passwords": lambda: passwords_dialog(self),
            "import-passwords": lambda: passwords_dialog(self, import_now=True),
            "fill-login": lambda: fill_login(self),
            "lock-vault": self.get_application().lock_vault,
            "shortcuts": lambda: shortcuts_dialog(self),
            "zoom-in": lambda: self._zoom(0.1),
            "zoom-out": lambda: self._zoom(-0.1),
            "zoom-reset": lambda: self.webview.set_zoom_level(1),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    def load_uri(self, uri):
        if not is_web_url(uri):
            self.toast("Mini Browser opens http:// and https:// websites.")
            return
        self.failed_uri = None
        self.stack.set_visible_child_name("web")
        self.webview.load_uri(uri)
        self.webview.grab_focus()

    def _create_webview(self, _view, _navigation):
        child = self.get_application().new_window(related_view=self.webview)
        child.stack.set_visible_child_name("web")
        # WebKit controls loading of the related view (including POST requests).
        return child.webview

    def _decide_policy(self, _view, decision, kind):
        if kind in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri != "about:blank" and not is_web_url(uri):
                decision.ignore()
                self.toast("This link uses an unsupported address type.")
                return True
        elif kind == WebKit.PolicyDecisionType.RESPONSE:
            if not decision.is_mime_type_supported():
                decision.download()
                return True
        return False

    def _load_changed(self, view, event):
        if event == WebKit.LoadEvent.STARTED:
            self.document_generation += 1
            self.failed = False
            self.stack.set_visible_child_name("web")
        elif event == WebKit.LoadEvent.FINISHED and not self.failed:
            uri = view.get_uri() or ""
            if is_web_url(uri):
                try:
                    self.library.record_visit(uri, view.get_title())
                    for window in self.get_application().get_windows():
                        if window.stack.get_visible_child_name() == "home":
                            window.refresh_recents()
                except Exception:
                    self.toast("Could not save this visit to history.")

    def _show_error(self, uri, message):
        self.failed = True
        self.failed_uri = uri
        self.error_page.set_description(message)
        self.stack.set_visible_child_name("error")

    def _load_failed(self, _view, _event, uri, error):
        if error.matches(WebKit.NetworkError.quark(), WebKit.NetworkError.CANCELLED):
            self.failed = True
            return True
        if self.failed:  # Keep the specific TLS message when both signals fire.
            return True
        self._show_error(uri, "Check the address and your internet connection, then try again.")
        return True

    def _tls_failed(self, _view, uri, _certificate, _errors):
        self._show_error(uri, "The website’s security certificate could not be verified.")
        return True

    def _web_process_terminated(self, *_):
        self._show_error(self.webview.get_uri(), "The page stopped responding. Reload to continue.")

    def _title_changed(self, view, _pspec):
        if self.stack.get_visible_child_name() == "web":
            self.set_title(view.get_title() or APP_NAME)

    def _retry(self, *_):
        if self.stack.get_visible_child_name() == "home":
            self.refresh_recents()
        elif self.failed_uri:
            self.load_uri(self.failed_uri)
        elif self.webview.get_uri():
            self.webview.reload()

    def _bookmark(self):
        uri = self.webview.get_uri() or ""
        if is_web_url(uri):
            try:
                self.library.add_bookmark(uri, self.webview.get_title())
                self.toast("Bookmark saved")
            except Exception:
                self.toast("Could not save this bookmark.")

    def _zoom(self, delta):
        self.webview.set_zoom_level(max(0.5, min(3, self.webview.get_zoom_level() + delta)))

    def toast(self, message):
        if not self._closing:
            self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _close_requested(self, *_):
        self._closing = True
        self.webview.stop_loading()
        return False
