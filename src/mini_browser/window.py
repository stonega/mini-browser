"""One WebView per portrait window with a native GNOME header bar."""

from datetime import datetime

from . import APP_NAME, autofill
from .audio_player import AudioPlayer
from .deepseek import COMMAND as DEEPSEEK_COMMAND
from .deepseek import ICON as DEEPSEEK_ICON
from .dialogs import (
    about_dialog,
    address_dialog,
    import_dialog,
    library_dialog,
    settings_dialog,
    shortcuts_dialog,
)
from .gtk import Adw, Gio, GLib, GObject, Gtk, Pango, Web
from .media import video_window_size
from .navigation import RETARGET, address_to_uri, is_web_url
from .password_dialogs import fill_login, passwords_dialog
from .script_loading import ScriptLoading
from .video_player import VideoPlayer
from .window_pin import WindowPin
from .window_size import DEFAULT_SIZE, WindowSizeMemory, script_app_key, web_app_key

DEFAULT_WIDTH, DEFAULT_HEIGHT = DEFAULT_SIZE
AUDIO_WIDTH = 420
AUDIO_HEIGHT = 200

AGENT_PROMPT = (
    "Use Mini Browser to open, manage and test the app I request. "
    "Discover commands with `mini-browser agent --help` and `mini-browser agent tools`. "
    "Enable control with `mini-browser --agent` if needed, then run "
    "`mini-browser agent list` to get page IDs. "
    "Use `mini-browser agent snapshot PAGE` for web elements and "
    "`mini-browser agent inspect PAGE` for native controls. "
    "Take a fresh snapshot before using element IDs. "
    "Save the app on Home with `mini-browser agent add-app URL --title 'App name'`. "
    "Verify the result with `mini-browser agent screenshot PAGE new.png --native`. "
    "You can also use Mini Browser's native MCP tools. "
    "From a source checkout, replace `mini-browser` with `./scripts/run`."
)


class HistoryItem(GObject.Object):
    def __init__(self, record):
        super().__init__()
        self.record = record


class BrowserWindow(Adw.ApplicationWindow):
    def __init__(self, app):
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
        self._deepseek_owner = None
        self._deepseek_pending = False
        self._deepseek_log_source = 0
        self.script_loading = None
        self.audio_mode = False
        self.video_mode = False
        self._video_available = False
        self._layout_source = 0
        self._layout_state = None
        self._header_source = 0
        self._header_pointer = False
        self._media_generation = 0
        self._media_drag_origin = None
        self.document_generation = 0
        self.webview = Web.View(network_session=app.network_session)
        self._user_content_manager = self.webview.get_user_content_manager()
        self._user_content_manager.add_script(
            Web.UserScript.new_for_world(
                RETARGET,
                Web.UserContentInjectedFrames.ALL_FRAMES,
                Web.UserScriptInjectionTime.START,
                "mini-browser-navigation",
                None,
                None,
            )
        )
        if not getattr(self._user_content_manager, "_mini_vault_world", False):
            # Register and retain the isolated world across asynchronous API calls.
            script = Web.UserScript.new_for_world(
                autofill.INITIALIZE,
                Web.UserContentInjectedFrames.TOP_FRAME,
                Web.UserScriptInjectionTime.START,
                autofill.WORLD,
                None,
                None,
            )
            self._user_content_manager.add_script(script)
            self._user_content_manager._mini_vault_world = True
        self.webview.connect("close", lambda *_: self.close())
        self.webview.connect("load-changed", self._load_changed)
        self.webview.connect("load-failed", self._load_failed)
        self.webview.connect("load-failed-with-tls-errors", self._tls_failed)
        self.webview.connect("notify::title", self._title_changed)
        self.webview.connect(
            "unsupported-uri", lambda *_: self.toast("This link uses an unsupported address type.")
        )
        self.webview.connect("web-process-terminated", self._web_process_terminated)
        self.webview.connect("notify::playing-audio", self._audio_changed)
        self.webview.connect("notify::is-muted", self._audio_changed)

        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self._home(), "home")
        self.stack.add_named(self.webview, "web")
        self.player = AudioPlayer(self.webview, self.toast)
        self.video_player = VideoPlayer(self.webview)
        self.stack.add_named(self.player, "audio")
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
        self.home_button = Gtk.Button(
            icon_name="go-home-symbolic",
            tooltip_text="Home (Alt+Home)",
            action_name="win.home",
        )
        self.headerbar.pack_start(self.home_button)
        self.back_button = Gtk.Button(
            icon_name="go-previous-symbolic",
            tooltip_text="Back (Alt+Left)",
            action_name="win.back",
        )
        self.back_history = Gtk.Popover(position=Gtk.PositionType.BOTTOM)
        self.back_history.set_parent(self.back_button)
        self.back_history.connect("notify::visible", self._menu_changed)
        history_press = Gtk.GestureLongPress(
            button=1, touch_only=False, propagation_phase=Gtk.PropagationPhase.CAPTURE
        )
        history_press.connect("pressed", self._back_history_pressed)
        self.back_button.add_controller(history_press)
        self.headerbar.pack_start(self.back_button)
        header_actions = Gtk.Box(spacing=0)
        self.pin_button = Gtk.ToggleButton(icon_name="view-pin-symbolic", tooltip_text="Pin on top")
        header_actions.append(self.pin_button)
        self.pin = WindowPin(self, self.pin_button)
        layouts = Gio.Menu()
        layouts.append("Full page", "win.page-layout")
        layouts.append("Audio player", "win.audio-player")
        layouts.append("Video player", "win.video-player")
        self.layout_button = Gtk.MenuButton(
            icon_name="view-paged-symbolic", tooltip_text="Page layout", menu_model=layouts
        )
        self.layout_button.get_popover().connect("notify::visible", self._layout_menu_changed)
        header_actions.append(self.layout_button)
        menu = Gio.Menu()
        menu.append("New Window", "app.new-window")
        menu.append("Refresh Page", "win.reload")
        menu.append("Reload Without Cache", "win.reload-bypass-cache")
        menu.append("Bookmark App", "win.bookmark")
        menu.append("Settings", "win.settings")
        menu.append(f"About {APP_NAME}", "win.about")
        self.menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", tooltip_text="Main menu", menu_model=menu
        )
        self.menu_button.get_popover().connect("notify::visible", self._menu_changed)
        header_actions.append(self.menu_button)
        self.headerbar.pack_end(header_actions)
        self.toolbar = Adw.ToolbarView(content=self.toasts)
        self.toolbar.add_top_bar(self.headerbar)
        self.set_content(self.toolbar)
        self._add_actions()
        self.webview.get_back_forward_list().connect("changed", self._navigation_changed)
        self.webview.connect("notify::uri", self._uri_changed)
        self.stack.connect("notify::visible-child-name", self._navigation_changed)
        self._navigation_changed()
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("enter", self._header_motion)
        motion.connect("motion", self._header_motion)
        motion.connect("leave", self._header_leave)
        self.toolbar.add_controller(motion)
        self.media_drag = Gtk.GestureDrag(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        self.media_drag.connect("drag-begin", self._media_drag_begin)
        self.media_drag.connect("drag-update", self._media_drag_update)
        self.media_drag.connect("drag-end", self._media_drag_end)
        self.stack.add_controller(self.media_drag)
        self.connect("notify::focus-widget", self._header_focus_changed)
        self._audio_changed()
        self.set_focus(self.home_entry)
        self.connect("close-request", self._close_requested)
        self.window_size = WindowSizeMemory(
            self, self.library, capture=self._normal_window_size, resize=self._resize_window
        )

    def _normal_window_size(self):
        if self.audio_mode or self.video_mode or self._layout_source:
            return None
        return self.get_default_size()

    def _resize_window(self, width, height):
        maximized, fullscreen = self._layout_state or (self.is_maximized(), self.is_fullscreen())
        self._resize_layout(width, height, maximized=maximized, fullscreen=fullscreen)

    def _select_window_size(self, uri):
        key = (
            script_app_key(DEEPSEEK_COMMAND)
            if self.get_application().deepseek.owns_uri(uri)
            else web_app_key(uri)
        )
        if key is not None:
            self.window_size.select(key)

    def _home(self):
        home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=16)
        self.home_sections = Gtk.Stack(hhomogeneous=False, vhomogeneous=False, vexpand=True)
        self.home_tabs = Gtk.StackSwitcher(stack=self.home_sections, halign=Gtk.Align.CENTER)
        home.append(self.home_tabs)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=8,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        self.home_entry = Gtk.Entry(
            placeholder_text="Search or enter an address",
            primary_icon_name="system-search-symbolic",
            secondary_icon_name="go-next-symbolic",
            height_request=48,
            margin_start=24,
            margin_end=24,
        )
        self.home_entry.set_icon_activatable(Gtk.EntryIconPosition.PRIMARY, False)
        self.home_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Open website")
        home.append(self.home_entry)

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
        self.app_tiles = self._tile_grid()
        content.append(self.app_tiles)
        content.append(self._agent_prompt())
        clamp = Adw.Clamp(maximum_size=420, tightening_threshold=360, child=content)
        self.home_sections.add_titled(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp), "apps", "Apps"
        )
        self.home_sections.add_titled(self._history_page(), "history", "History")
        self.home_sections.connect("notify::visible-child-name", self._home_section_changed)
        home.append(self.home_sections)
        self.refresh_recents()
        return home

    def _agent_prompt(self):
        self.agent_prompt = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            css_classes=["card"],
        )
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        body.append(Gtk.Label(label="Agent prompt", xalign=0, css_classes=["heading"]))
        self.agent_prompt_text = Gtk.Label(
            label=AGENT_PROMPT,
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            css_classes=["monospace"],
        )
        body.append(self.agent_prompt_text)
        self.copy_agent_prompt_button = Gtk.Button(label="Copy prompt", halign=Gtk.Align.START)
        self.copy_agent_prompt_button.connect("clicked", self._copy_agent_prompt)
        body.append(self.copy_agent_prompt_button)
        self.agent_prompt.append(body)
        return self.agent_prompt

    def _copy_agent_prompt(self, _button):
        self.get_clipboard().set(AGENT_PROMPT)
        self.toast("Agent prompt copied")

    def _history_page(self):
        self.history_model = Gio.ListStore.new(HistoryItem)
        self._history_records = None
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._history_setup)
        factory.connect("bind", self._history_bind)
        self.history_list = Gtk.ListView(
            model=Gtk.NoSelection(model=self.history_model),
            factory=factory,
            single_click_activate=True,
            show_separators=True,
        )
        self.history_list.connect(
            "activate", lambda _view, index: self.load_uri(self.history_model[index].record["url"])
        )
        self.history_empty = Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title="No history yet",
            description="Pages you visit will appear here.",
        )
        self.history_stack = Gtk.Stack()
        self.history_stack.add_named(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.history_list),
            "list",
        )
        self.history_stack.add_named(self.history_empty, "empty")
        return self.history_stack

    @staticmethod
    def _history_setup(_factory, item):
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        heading = Gtk.Box(spacing=8)
        item.title_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        item.time_label = Gtk.Label(css_classes=["caption", "dim-label"])
        heading.append(item.title_label)
        heading.append(item.time_label)
        item.url_label = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption", "dim-label"]
        )
        body.append(heading)
        body.append(item.url_label)
        item.set_child(body)

    @staticmethod
    def _history_bind(_factory, item):
        record = item.get_item().record
        item.title_label.set_label(" ".join((record["title"] or record["url"]).split()))
        item.url_label.set_label(record["url"])
        try:
            visited = datetime.fromtimestamp(record["last_visit"]).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            visited = ""
        item.time_label.set_label(visited)
        item.get_child().set_tooltip_text(f"{record['title']}\n{record['url']}\n{visited}")

    def _home_section_changed(self, *_):
        if self.home_sections.get_visible_child_name() == "history":
            self._refresh_history()

    def _refresh_history(self):
        records = self.library.search("history", limit=None)
        if records != self._history_records:
            self.history_model.splice(
                0, self.history_model.get_n_items(), [HistoryItem(record) for record in records]
            )
            self._history_records = records
        self.history_stack.set_visible_child_name("list" if records else "empty")

    @staticmethod
    def _tile_grid():
        return Gtk.FlowBox(
            css_classes=["recent-sites"],
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=3,
            column_spacing=12,
            row_spacing=12,
        )

    def refresh_recents(self):
        """Refresh app launchers and the history tab when it is visible."""
        self.app_tiles.remove_all()
        saved = self.library.home_apps()
        saved_sites = {app["site"] for app in saved}
        records = saved + [
            app for app in self.library.bookmarked_apps() if app["site"] not in saved_sites
        ]
        devices = self.get_application().device_tiles()
        icon = Gtk.Image.new_from_file(str(DEEPSEEK_ICON))
        icon.set_pixel_size(44)
        self.deepseek_button = self._app_tile(
            "DeepSeek Harness",
            "Starting…" if self._deepseek_pending else "Local app",
            icon,
            self._open_deepseek,
        )
        self.deepseek_button.set_sensitive(not self._deepseek_pending)
        for device, subtitle in devices:
            body = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
                margin_top=12,
                margin_bottom=12,
                margin_start=4,
                margin_end=4,
                halign=Gtk.Align.CENTER,
            )
            body.append(Gtk.Image(icon_name="phone-symbolic", pixel_size=44))
            body.append(
                Gtk.Label(
                    label=device.name,
                    lines=2,
                    wrap=True,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=10,
                    width_chars=8,
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
            button = Gtk.Button(
                child=body, css_classes=["flat", "card"], tooltip_text=f"{device.name}\n{subtitle}"
            )
            button.connect(
                "clicked",
                lambda _button, device_id=device.id: self.get_application().open_device(device_id),
            )
            self.app_tiles.append(button)
        self.app_tiles.append(self.deepseek_button)
        for record in records:
            host = record["site"].split("://", 1)[1].removeprefix("www.")
            title = " ".join((record["title"] or host).split())
            button = self._app_tile(
                title,
                host,
                Adw.Avatar(size=44, text=host, show_initials=True),
                lambda _button, url=record["url"]: self.load_uri(url),
            )
            button.set_tooltip_text(f"{title}\n{record['url']}")
            self.app_tiles.append(button)
        self._home_section_changed()

    @staticmethod
    def _app_tile(title, subtitle, icon, callback):
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=4,
            margin_end=4,
            halign=Gtk.Align.CENTER,
        )
        body.append(icon)
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
                label=subtitle,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=12,
                css_classes=["caption", "dim-label"],
            )
        )
        button = Gtk.Button(
            child=body, css_classes=["flat", "card"], tooltip_text=f"{title}\n{subtitle}"
        )
        button.connect("clicked", callback)
        return button

    def _open_deepseek(self, *_):
        if not self._closing:
            window = self.get_application().new_window()
            window._start_deepseek()
            return window

    def _start_deepseek(self):
        if self._closing or self._deepseek_pending:
            return
        self._release_deepseek()
        owner = self._deepseek_owner = object()
        self._deepseek_pending = True
        self._restore_page()
        self.window_size.select(script_app_key(DEEPSEEK_COMMAND))
        self.refresh_recents()
        if self.script_loading is None:
            self.script_loading = ScriptLoading()
            self.stack.add_named(self.script_loading, "script-loading")
        self.script_loading.start()
        self.stack.set_visible_child_name("script-loading")
        self.set_title("DeepSeek Harness")

        def update_log():
            if self._closing or self._deepseek_owner is not owner or not self._deepseek_pending:
                self._deepseek_log_source = 0
                return GLib.SOURCE_REMOVE
            self.script_loading.set_log(self.get_application().deepseek.startup_log(owner))
            return GLib.SOURCE_CONTINUE

        self._deepseek_log_source = GLib.timeout_add(100, update_log)

        def changed(uri, error):
            def update():
                if self._closing or self._deepseek_owner is not owner:
                    return GLib.SOURCE_REMOVE
                self._deepseek_pending = False
                self._stop_deepseek_loading()
                if error:
                    self.show_home()
                    dialog = Adw.AlertDialog(heading="DeepSeek Harness stopped", body=error)
                    dialog.add_response("close", "Close")
                    dialog.add_response("retry", "Try again")
                    dialog.set_default_response("retry")
                    dialog.set_close_response("close")
                    dialog.connect(
                        "response", lambda _d, answer: answer == "retry" and self._start_deepseek()
                    )
                    dialog.present(self)
                else:
                    self.load_uri(uri)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update)

        self.get_application().deepseek.acquire(owner, changed)

    def _stop_deepseek_loading(self):
        if self._deepseek_log_source:
            GLib.source_remove(self._deepseek_log_source)
            self._deepseek_log_source = 0
        if self.script_loading is not None:
            self.script_loading.stop()

    def _release_deepseek(self):
        owner, self._deepseek_owner = self._deepseek_owner, None
        self._deepseek_pending = False
        self._stop_deepseek_loading()
        if owner is not None:
            self.get_application().deepseek.release(owner)

    def show_home(self):
        self._release_deepseek()
        self._restore_page()
        self.window_size.select(None)
        self.webview.stop_loading()
        self.refresh_recents()
        self.stack.set_visible_child_name("home")
        self.set_title(APP_NAME)
        self.home_entry.set_text("")
        self.home_entry.grab_focus()
        self._audio_changed()

    def _audio_changed(self, *_):
        if self._closing:
            return
        available = (
            self.stack.get_visible_child_name() == "web"
            and not self.webview.is_loading()
            and not self.failed
            and self.webview.is_playing_audio()
        )
        self.lookup_action("audio-player").set_enabled(available and not self.audio_mode)
        self.lookup_action("page-layout").set_enabled(self.audio_mode or self.video_mode)
        self.lookup_action("video-player").set_enabled(
            self.stack.get_visible_child_name() in ("web", "audio")
            and not self.webview.is_loading()
            and not self.failed
            and self._video_available
            and not self.video_mode
        )
        self.layout_button.set_icon_name(
            "audio-x-generic-symbolic"
            if self.audio_mode
            else "video-x-generic-symbolic"
            if self.video_mode
            else "view-paged-symbolic"
        )
        if self.audio_mode:
            self.player.refresh()

    def _layout_menu_changed(self, popover, *_):
        if popover.get_visible():
            self._probe_video()
        self._menu_changed(popover)

    def _menu_changed(self, popover, *_):
        if popover.get_visible():
            self._cancel_header_hide()
        else:
            self._queue_header_hide()

    def _navigation_changed(self, *_):
        if self.back_history.get_visible():
            self.back_history.popdown()
        browsing = self.stack.get_visible_child_name() != "home" and not self._closing
        media = self.audio_mode or self.video_mode
        can_go_back = media or self.webview.can_go_back()
        self.back_button.set_visible(browsing and can_go_back)
        self.lookup_action("back").set_enabled(browsing and can_go_back)
        self.back_button.set_tooltip_text("Return to Full page" if media else "Back (Alt+Left)")
        self.lookup_action("forward").set_enabled(browsing and self.webview.can_go_forward())
        self.lookup_action("reload-bypass-cache").set_enabled(
            browsing and bool(self.webview.get_uri())
        )
        uri = self.webview.get_uri() or ""
        self.lookup_action("bookmark").set_enabled(
            browsing and is_web_url(uri) and not self.get_application().deepseek.owns_uri(uri)
        )

    def _back(self):
        if self.audio_mode or self.video_mode:
            self._restore_page()
        else:
            self.webview.go_back()

    def _back_history_pressed(self, gesture, _x, _y):
        if not self.lookup_action("back").get_enabled():
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._show_back_history()

    def _show_back_history(self):
        if self._closing:
            return
        self.toolbar.set_reveal_top_bars(True)
        history = self.webview.get_back_forward_list()
        entries = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        entries.append(
            Gtk.Label(
                label="Back history",
                xalign=0,
                css_classes=["heading"],
                margin_start=8,
                margin_end=8,
                margin_top=4,
                margin_bottom=6,
            )
        )
        for offset in range(1, 21):
            item = history.get_nth_item(-offset)
            if item is None:
                break
            uri = item.get_uri() or ""
            title = " ".join((item.get_title() or uri or "Untitled page").split())
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            body.append(
                Gtk.Label(
                    label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26
                )
            )
            body.append(
                Gtk.Label(
                    label=uri,
                    xalign=0,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=32,
                    css_classes=["caption", "dim-label"],
                )
            )
            button = Gtk.Button(child=body, css_classes=["flat"], tooltip_text=f"{title}\n{uri}")
            button.connect("clicked", lambda _button, target=item: self._jump_back(target))
            entries.append(button)
        if history.get_back_item() is None:
            entries.append(
                Gtk.Label(
                    label="No previous pages",
                    css_classes=["dim-label"],
                    margin_start=8,
                    margin_end=8,
                    margin_top=8,
                    margin_bottom=8,
                )
            )
        self.back_history.set_child(
            Gtk.ScrolledWindow(
                child=entries,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                min_content_width=240,
                max_content_width=300,
                max_content_height=360,
                propagate_natural_width=True,
                propagate_natural_height=True,
            )
        )
        self.back_history.popup()

    def _jump_back(self, item):
        self.back_history.popdown()
        if item not in self.webview.get_back_forward_list().get_back_list_with_limit(20):
            return
        self._restore_page()
        self.webview.go_to_back_forward_list_item(item)

    def _uri_changed(self, *_):
        if (
            self._deepseek_owner is not None
            and not self._deepseek_pending
            and not self.get_application().deepseek.owns_uri(self.webview.get_uri() or "")
        ):
            self._release_deepseek()
        if self._closing:
            return
        # History API and fragment navigation do not emit load-changed.
        self._restore_page()
        if self.stack.get_visible_child_name() != "home":
            self._select_window_size(self.webview.get_uri() or "")
        self._navigation_changed()

    def _cancel_header_hide(self):
        if self._header_source:
            GLib.source_remove(self._header_source)
            self._header_source = 0

    def _set_media_header(self, immersive):
        self._media_generation += 1
        self._media_drag_origin = None
        self.media_drag.reset()
        self._cancel_header_hide()
        self._header_pointer = False
        self.toolbar.set_extend_content_to_top_edge(immersive)
        self.toolbar.set_reveal_top_bars(not immersive)
        if immersive:
            self.headerbar.add_css_class("osd")
        else:
            self.headerbar.remove_css_class("osd")
        self._navigation_changed()

    def _media_drag_begin(self, gesture, x, y):
        self._media_drag_origin = None
        target = self.stack.pick(x, y, Gtk.PickFlags.DEFAULT)
        allowed = (self.audio_mode or self.video_mode) and not self.get_visible_dialog()
        while target and target != self.stack:
            if isinstance(target, (Gtk.Button, Gtk.Range, Gtk.Editable)):
                allowed = False
                break
            target = target.get_parent()
        if self.video_mode:
            # Keep the native seek/volume control strip available for scrubbing.
            valid, bounds = self.webview.compute_bounds(self.stack)
            if valid and y >= bounds.get_y() + bounds.get_height() - min(
                72, bounds.get_height() / 3
            ):
                allowed = False
        event = gesture.get_current_event()
        if not allowed or event is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        valid, surface_x, surface_y = event.get_position()
        if not valid:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._media_drag_origin = (surface_x, surface_y, event.get_time())
        # The web surface claims presses immediately. Hold this sequence until it is
        # either a window drag or a click replayed by GTK on denial below.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _media_drag_update(self, gesture, offset_x, offset_y):
        origin = self._media_drag_origin
        if origin is None or not self.stack.drag_check_threshold(
            0, 0, int(offset_x), int(offset_y)
        ):
            return
        self._media_drag_origin = None
        self.get_surface().begin_move(gesture.get_device(), gesture.get_current_button(), *origin)
        gesture.reset()

    def _media_drag_end(self, gesture, *_):
        if self._media_drag_origin is not None:
            self._media_drag_origin = None
            gesture.set_state(Gtk.EventSequenceState.DENIED)

    def _header_has_focus(self):
        focus = self.get_focus()
        return focus is not None and (focus == self.headerbar or focus.is_ancestor(self.headerbar))

    def _show_header(self):
        self._cancel_header_hide()
        self.toolbar.set_reveal_top_bars(True)
        self.layout_button.grab_focus()

    def _queue_header_hide(self):
        self._cancel_header_hide()
        if not self._closing and (self.audio_mode or self.video_mode):
            self._header_source = GLib.timeout_add(1500, self._hide_media_header)

    def _hide_media_header(self):
        self._header_source = 0
        if not (
            self._header_pointer
            or self._header_has_focus()
            or self.layout_button.get_popover().get_visible()
            or self.menu_button.get_popover().get_visible()
            or self.back_history.get_visible()
        ):
            self.toolbar.set_reveal_top_bars(False)
        return GLib.SOURCE_REMOVE

    def _header_motion(self, _controller, _x, y):
        if not (self.audio_mode or self.video_mode):
            return
        revealed = self.toolbar.get_reveal_top_bars()
        self._header_pointer = y <= (max(48, self.headerbar.get_height()) if revealed else 8)
        if self._header_pointer:
            self._cancel_header_hide()
            self.toolbar.set_reveal_top_bars(True)
        elif revealed:
            self._queue_header_hide()

    def _header_leave(self, *_):
        self._header_pointer = False
        self._queue_header_hide()

    def _header_focus_changed(self, *_):
        if self.audio_mode or self.video_mode:
            if self._header_has_focus():
                self._cancel_header_hide()
                self.toolbar.set_reveal_top_bars(True)
            else:
                self._queue_header_hide()

    def _probe_video(self):
        generation = self.document_generation

        def probed(available):
            if not self._closing and generation == self.document_generation:
                self._video_available = available
                self._audio_changed()

        self.video_player.probe(probed)

    def _remember_page_layout(self):
        if not self.audio_mode and not self.video_mode:
            self.window_size.capture()
            self._page_size = self.get_default_size()
            self._page_maximized = self.is_maximized()
            self._page_fullscreen = self.is_fullscreen()
            self._page_pinned = (
                self.pin.pinned if self.pin.requested is None else self.pin.requested
            )

    def _show_audio_player(self):
        if not self.lookup_action("audio-player").get_enabled():
            return
        self._remember_page_layout()
        if self.video_mode:
            self.video_player.leave()
        self.video_mode = False
        self.audio_mode = True
        self.pin.set_pinned(True)
        self.stack.set_visible_child_name("audio")
        self._set_media_header(True)
        self._resize_layout(AUDIO_WIDTH, AUDIO_HEIGHT)
        self.player.mute_button.grab_focus()
        self._audio_changed()

    def _show_video_player(self):
        if not self.lookup_action("video-player").get_enabled():
            return
        self._remember_page_layout()
        self.audio_mode = False
        self.video_mode = True
        self.pin.set_pinned(True)
        self.stack.set_visible_child_name("web")
        self._set_media_header(True)
        self.webview.grab_focus()
        self._audio_changed()
        generation = self.document_generation
        media_generation = self._media_generation

        def entered(dimensions):
            if (
                self._closing
                or generation != self.document_generation
                or not self.video_mode
                or media_generation != self._media_generation
            ):
                return
            if not dimensions:
                self._restore_page()
                self.toast("Could not open video mode. Use the website’s video controls.")
                return
            monitor = self.get_display().get_monitor_at_surface(self.get_surface())
            geometry = monitor.get_geometry() if monitor else None
            self._resize_layout(
                *video_window_size(
                    dimensions["width"],
                    dimensions["height"],
                    max_width=int(geometry.width * 0.9) if geometry else 960,
                    max_height=int(geometry.height * 0.8) if geometry else 720,
                )
            )

        self.video_player.enter(entered)

    def _restore_page(self):
        if not self.audio_mode and not self.video_mode:
            return
        if self.video_mode:
            self.video_player.leave()
        self.audio_mode = False
        self.video_mode = False
        self.pin.set_pinned(self._page_pinned)
        self._set_media_header(False)
        self.stack.set_visible_child_name("web")
        self._resize_layout(
            *self._page_size,
            maximized=self._page_maximized,
            fullscreen=self._page_fullscreen,
        )
        self._audio_changed()
        self.webview.grab_focus()

    def _stop(self):
        if self.audio_mode or self.video_mode:
            self._restore_page()
        else:
            self.webview.stop_loading()

    def _resize_layout(self, width, height, *, maximized=False, fullscreen=False):
        if self._layout_source:
            GLib.source_remove(self._layout_source)
        self._layout_state = (maximized, fullscreen)
        if self.is_fullscreen():
            self.unfullscreen()
        if self.is_maximized():
            self.unmaximize()
        self.set_default_size(width, height)
        deadline = GLib.get_monotonic_time() + 2_000_000

        def finish():
            if self._closing:
                self._layout_source = 0
                return GLib.SOURCE_REMOVE
            normal = not self.is_maximized() and not self.is_fullscreen()
            sized = (self.get_width(), self.get_height()) == (width, height)
            if normal and not sized and GLib.get_monotonic_time() < deadline:
                # A configure event can restore the old normal size after our
                # first resize request. Wait for the requested size before
                # maximizing again so GTK remembers the right normal geometry.
                self.set_default_size(width, height)
                return GLib.SOURCE_CONTINUE
            if not normal and GLib.get_monotonic_time() < deadline:
                return GLib.SOURCE_CONTINUE
            self._layout_source = 0
            self._layout_state = None
            self.window_size.capture()
            if maximized:
                self.maximize()
            if fullscreen:
                self.fullscreen()
            return GLib.SOURCE_REMOVE

        self._layout_source = GLib.timeout_add(50, finish)

    def _add_actions(self):
        for name, callback in {
            "address": lambda: address_dialog(self),
            "home": self.show_home,
            "settings": lambda: settings_dialog(self),
            "devices": self._devices_dialog,
            "about": lambda: about_dialog(self),
            "show-header": self._show_header,
            "page-layout": self._restore_page,
            "audio-player": self._show_audio_player,
            "video-player": self._show_video_player,
            "close": self.close,
            "reload": self._retry,
            "reload-bypass-cache": self.webview.reload_bypass_cache,
            "back": self._back,
            "forward": self.webview.go_forward,
            "stop": self._stop,
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

    def _devices_dialog(self):
        from .device_dialogs import devices_dialog

        devices_dialog(self)

    def load_uri(self, uri):
        if not is_web_url(uri):
            self.toast("Mini Browser opens http:// and https:// websites.")
            return
        if not self.get_application().deepseek.owns_uri(uri):
            self._release_deepseek()
        self.failed_uri = None
        self._restore_page()
        self._select_window_size(uri)
        self.stack.set_visible_child_name("web")
        self.webview.load_uri(uri)
        self.webview.grab_focus()

    def _load_changed(self, view, event):
        if self._closing:
            return
        if event == Web.LoadEvent.STARTED:
            self.player.reset()
            self.video_player.reset()
            self._video_available = False
            self._restore_page()
            self.document_generation += 1
            self.failed = False
            self.stack.set_visible_child_name("web")
        elif event == Web.LoadEvent.FINISHED and not self.failed:
            self._probe_video()
            uri = view.get_uri() or ""
            if is_web_url(uri) and not self.get_application().deepseek.owns_uri(uri):
                try:
                    self.library.record_visit(uri, view.get_title())
                    for window in self.get_application().get_windows():
                        if (
                            isinstance(window, BrowserWindow)
                            and window.stack.get_visible_child_name() == "home"
                        ):
                            window.refresh_recents()
                except Exception:
                    self.toast("Could not save this visit to history.")
        self._navigation_changed()
        self._audio_changed()

    def _show_error(self, uri, message):
        if self._closing:
            return
        self.failed = True
        self.failed_uri = uri
        self.player.reset()
        self.video_player.reset()
        self._video_available = False
        self._restore_page()
        self.error_page.set_description(message)
        self.stack.set_visible_child_name("error")
        self._audio_changed()

    def _load_failed(self, _view, _event, uri, error):
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
        if self._closing:
            return
        if self.stack.get_visible_child_name() in ("web", "audio"):
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
        if self.lookup_action("bookmark").get_enabled() and is_web_url(uri):
            try:
                self.library.add_bookmark_app(uri)
                for window in self.get_application().get_windows():
                    if isinstance(window, BrowserWindow):
                        window.refresh_recents()
                self.toast("App bookmarked")
            except Exception:
                self.toast("Could not save this bookmark.")

    def _zoom(self, delta):
        self.webview.set_zoom_level(max(0.5, min(3, self.webview.get_zoom_level() + delta)))

    def toast(self, message):
        if not self._closing:
            self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _close_requested(self, *_):
        self.window_size.close()
        self._closing = True
        self._release_deepseek()
        self.back_history.popdown()
        self.back_history.unparent()
        self.pin.close()
        self._cancel_header_hide()
        if self._layout_source:
            GLib.source_remove(self._layout_source)
            self._layout_source = 0
        self.player.reset()
        self.video_player.reset()
        self.webview.stop_loading()
        self.webview.set_is_muted(True)
        self.webview.close()
        return False
