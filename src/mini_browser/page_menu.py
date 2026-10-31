"""Native GTK page menus backed by Chromium's hit-test and edit state."""

import base64
import json

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .page_context import COPY, CUT, PASTE, agent_prompt, menu_groups, search_url


class PageMenu:
    def __init__(self, view, context):
        self.view, self.context = view, context
        self.generation = view.generation
        self.answered = False
        self.buttons = {}
        self.popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for index, group in enumerate(menu_groups(context)):
            if index:
                box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
            for item in group:
                button = Gtk.Button(label=item.label, css_classes=["flat"])
                button.get_child().set_xalign(0)
                button.set_sensitive(item.enabled)
                button.connect("clicked", lambda _b, action=item.action: self.activate(action))
                self.buttons[item.action] = button
                box.append(button)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=max(120, min(600, view.get_height() - 40)),
            child=box,
        )
        self.popover.set_child(scroll)
        self.popover.set_parent(view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = context["x"], context["y"], 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.connect("closed", self._closed)
        self.popover.popup()

    def _reply(self, command=None):
        if self.answered:
            return
        self.answered = True
        self.view.bridge.reply(
            self.context["request"], command is not None, json.dumps({"command": command}).encode()
        )

    def _closed(self, *_):
        self._reply()
        if self.view.context_menu is self:
            self.view.context_menu = None
        self.popover.unparent()

    def close(self):
        self._reply()
        self.popover.popdown()

    def current(self):
        return not self.view.closed and self.view.generation == self.generation

    def activate(self, action):
        button = self.buttons.get(action)
        if self.answered or not self.current() or not button or not button.get_sensitive():
            return
        view, context = self.view, self.context
        # Keep Chromium's originating frame for edit/navigation commands. Paste
        # uses GTK's desktop clipboard because windowless CEF has a private one.
        self._reply(action if isinstance(action, int) and action != PASTE else None)
        self.popover.popdown()
        if action in (COPY, CUT):
            view.get_clipboard().set(context.get("selection", ""))
        elif action == PASTE:
            view.grab_focus()
            view.bridge.focus(view.identifier, 1)
            view._clipboard("paste")
        elif isinstance(action, int):
            return
        elif action == "agent":
            view.get_clipboard().set(
                agent_prompt(context, title=view.get_title(), page_id=view.identifier)
            )
            self.toast("Agent prompt copied")
        elif action.startswith("copy-"):
            key = {"copy-page": "page_url", "copy-link": "link_url", "copy-media": "source_url"}
            view.get_clipboard().set(context.get(key[action], ""))
        elif action in ("open-link", "open-media", "search"):
            uri = (
                search_url(context["selection"])
                if action == "search"
                else context["link_url" if action == "open-link" else "source_url"]
            )
            root = view.get_root()
            app = root.get_application() if root else None
            if app and hasattr(app, "new_window"):
                app.new_window(uri)
            else:
                view.emit("new-window", uri)
        elif action in ("save-link", "save-media"):
            uri = context["link_url" if action == "save-link" else "source_url"]
            view.bridge.start_download(view.identifier, uri.encode())
        elif action == "save-page":
            self.capture("Page.captureSnapshot", {"format": "mhtml"}, "page.mhtml")
        elif action == "save-pdf":
            self.capture("Page.printToPDF", {"printBackground": True}, "page.pdf", encoded=True)
        elif action == "print":
            self.capture("Page.printToPDF", {"printBackground": True}, None, encoded=True)
        elif action == "source":
            self.source()
        else:
            view.activate_action("win." + action, None)

    def toast(self, message):
        root = self.view.get_root()
        if root and hasattr(root, "toast"):
            root.toast(message)

    def capture(self, method, parameters, filename, *, encoded=False):
        def captured(result, error):
            if not self.current():
                return
            if error or not result or not result.get("data"):
                self.toast("Could not save the page. Reload it and try again.")
                return
            content = base64.b64decode(result["data"]) if encoded else result["data"].encode()
            if filename is None:
                self.print_page(content)
            else:
                self.save(content, filename)

        self.view.command(method, parameters, captured)

    def save(self, content, filename):
        picker = Gtk.FileDialog(title="Save page", initial_name=filename)

        def saved(file, result):
            try:
                file.replace_contents_finish(result)
                self.toast("Page saved")
            except GLib.Error:
                self.toast("Could not write the file. Choose a writable location and try again.")

        def selected(picker, result):
            try:
                file = picker.save_finish(result)
            except GLib.Error:
                return
            file.replace_contents_bytes_async(
                GLib.Bytes.new(content), None, False, Gio.FileCreateFlags.PRIVATE, None, saved
            )

        picker.save(self.view.get_root(), None, selected)

    def print_page(self, content):
        try:
            file, stream = Gio.File.new_tmp("mini-browser-print-XXXXXX.pdf")
            stream.close(None)
        except GLib.Error:
            self.toast("Could not prepare the page for printing. Try Save as PDF instead.")
            return

        def cleanup():
            try:
                file.delete(None)
            except GLib.Error:
                pass

        def printed(dialog, result):
            try:
                dialog.print_file_finish(result)
            except GLib.Error as error:
                if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self.toast("Could not print the page. Try Save as PDF instead.")
            finally:
                cleanup()

        def written(file, result):
            try:
                file.replace_contents_finish(result)
                if not self.current():
                    cleanup()
                    return
                dialog = Gtk.PrintDialog(title="Print Page")
                dialog.print_file(self.view.get_root(), None, file, None, printed)
            except GLib.Error:
                cleanup()
                self.toast("Could not prepare the page for printing. Try Save as PDF instead.")

        file.replace_contents_bytes_async(
            GLib.Bytes.new(content), None, False, Gio.FileCreateFlags.PRIVATE, None, written
        )

    def source(self):
        def document(result, error):
            if not self.current():
                return
            if error or not result:
                self.toast("Could not read page source. Reload it and try again.")
                return
            self.view.command("DOM.getOuterHTML", {"nodeId": result["root"]["nodeId"]}, received)

        def received(result, error):
            if not self.current():
                return
            if error or not result:
                self.toast("Could not read page source. Reload it and try again.")
                return
            text = Gtk.TextView(editable=False, monospace=True, left_margin=12, right_margin=12)
            text.get_buffer().set_text(result["outerHTML"])
            dialog = Adw.Dialog(title="Page Source", content_width=700, content_height=600)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            toolbar.set_content(Gtk.ScrolledWindow(child=text))
            dialog.set_child(toolbar)
            self.view.dialogs.add(dialog)
            dialog.connect("closed", lambda *_: self.view.dialogs.discard(dialog))
            dialog.present(self.view.get_root())

        self.view.command("DOM.getDocument", {}, document)
