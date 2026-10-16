"""One WebView per portrait window, with one persistent close control."""

from . import APP_NAME
from .dialogs import address_dialog, import_dialog, library_dialog, shortcuts_dialog
from .gtk import Adw, Gio, Gtk, WebKit
from .navigation import address_to_uri, is_web_url

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
        if related_view:
            self.webview = WebKit.WebView(related_view=related_view)
        else:
            self.webview = WebKit.WebView(network_session=app.network_session)
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
        overlay = Gtk.Overlay(child=self.toasts)
        close_button = Gtk.Button(
            icon_name="window-close-symbolic",
            tooltip_text="Close window (Ctrl+W)",
            width_request=34,
            height_request=34,
            css_classes=["circular", "mini-close"],
        )
        close_button.connect("clicked", lambda *_: self.close())
        # The control can also provide native window dragging without a title bar.
        handle = Gtk.WindowHandle(
            child=close_button,
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            margin_top=8,
            margin_end=8,
        )
        overlay.add_overlay(handle)
        self.set_content(overlay)
        self._add_actions()
        self.connect("close-request", self._close_requested)

    def _home(self):
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            valign=Gtk.Align.CENTER,
            margin_top=64,
            margin_bottom=40,
            margin_start=24,
            margin_end=24,
        )
        content.append(Gtk.Image(icon_name="web-browser-symbolic", pixel_size=56))
        title = Gtk.Label(label="Mini Browser", css_classes=["title-1"])
        content.append(title)
        content.append(
            Gtk.Label(
                label="One page. One little window.",
                css_classes=["dim-label"],
            )
        )
        entry = Gtk.Entry(
            placeholder_text="Search or enter an address",
            primary_icon_name="system-search-symbolic",
            margin_top=16,
        )
        content.append(entry)
        open_button = Gtk.Button(label="Open website", css_classes=["suggested-action", "pill"])

        def open_address(*_):
            try:
                self.load_uri(address_to_uri(entry.get_text()))
            except ValueError as exc:
                self.toast(str(exc))

        entry.connect("activate", open_address)
        open_button.connect("clicked", open_address)
        content.append(open_button)
        import_button = Gtk.Button(label="Import from Chrome", css_classes=["flat"], margin_top=12)
        import_button.connect("clicked", lambda *_: import_dialog(self))
        content.append(import_button)
        library_button = Gtk.Button(label="Bookmarks & history", css_classes=["flat"])
        library_button.connect("clicked", lambda *_: library_dialog(self))
        content.append(library_button)
        help_button = Gtk.Button(
            label="Keyboard shortcuts",
            css_classes=["flat", "dim-label"],
            margin_top=12,
        )
        help_button.connect("clicked", lambda *_: shortcuts_dialog(self))
        content.append(help_button)
        clamp = Adw.Clamp(maximum_size=380, tightening_threshold=300, child=content)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)

    def _add_actions(self):
        for name, callback in {
            "address": lambda: address_dialog(self),
            "close": self.close,
            "reload": self._retry,
            "back": self.webview.go_back,
            "forward": self.webview.go_forward,
            "stop": self.webview.stop_loading,
            "import": lambda: import_dialog(self),
            "library": lambda: library_dialog(self),
            "bookmark": self._bookmark,
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
            self.failed = False
            self.stack.set_visible_child_name("web")
        elif event == WebKit.LoadEvent.FINISHED and not self.failed:
            uri = view.get_uri() or ""
            if is_web_url(uri):
                try:
                    self.library.record_visit(uri, view.get_title())
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
        self.set_title(view.get_title() or APP_NAME)

    def _retry(self, *_):
        if self.failed_uri:
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
