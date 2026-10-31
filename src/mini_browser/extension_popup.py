"""Extension-owned pages in independent, origin-restricted GTK windows."""

from .gtk import Adw, Gio, Gtk, Web
from .navigation import is_web_url


class ExtensionPopupWindow(Adw.ApplicationWindow):
    def __init__(self, app, extension, uri):
        super().__init__(
            application=app,
            title=f"{extension['name']} — Extension",
            default_width=420,
            default_height=780,
        )
        self._closing = False
        self.extension = extension
        self.uri = uri
        # Keep privileged extension pages out of the browser's agent/page list.
        # They receive no BrowserWindow scripts, vault integration or history.
        self.view = Web.View(
            network_session=app.network_session, extension_origin=extension["origin"]
        )
        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self.view, "web")
        error = Adw.StatusPage(
            title="Could not open extension page",
            description="Check that the extension is enabled and its files are still available.",
            icon_name="dialog-error-symbolic",
        )
        retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self._reload())
        error.set_child(retry)
        self.stack.add_named(error, "error")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(
            Adw.HeaderBar(
                title_widget=Adw.WindowTitle(title=extension["name"], subtitle="Extension")
            )
        )
        self.set_content(toolbar)
        for name, callback in {
            "close": self.close,
            "stop": self.close,
            "reload": self._reload,
            "reload-bypass-cache": self._reload,
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        self.view.connect("close", lambda *_: self.close())
        self.view.connect("new-window", self._new_window)
        self.view.connect("load-failed", self._failed)
        self.view.connect("web-process-terminated", self._failed)
        self.view.connect("permission-request", lambda _view, request: request.deny())
        self.view.connect(
            "unsupported-uri", lambda *_: self.toast("This page belongs to another extension.")
        )
        self.connect("close-request", self._close_requested)
        self.view.load_uri(uri)

    def toast(self, message):
        if not self._closing:
            self.toasts.add_toast(Adw.Toast(title=message))

    def _reload(self):
        self.stack.set_visible_child_name("web")
        self.view.load_uri(self.uri)

    def _failed(self, *_):
        if not self._closing:
            self.stack.set_visible_child_name("error")
        return True

    def _new_window(self, _view, uri):
        app = self.get_application()
        if is_web_url(uri):
            app.new_window(uri)
        else:
            app.open_extension_uri(uri)

    def _close_requested(self, *_):
        self._closing = True
        app = self.get_application()
        if app.extension_popup_windows.get(self.uri) is self:
            app.extension_popup_windows.pop(self.uri)
        self.view.close()
        return False
