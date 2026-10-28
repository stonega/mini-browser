"""Agent commands on the GTK main thread, using the existing in-process CEF API."""

import base64
import math
import sqlite3

from .control import ControlError
from .gtk import GLib, Gtk
from .navigation import address_to_uri
from .storage import home_app_url

ACTIONS = frozenset(
    (
        "home",
        "back",
        "forward",
        "reload",
        "reload-bypass-cache",
        "stop",
        "address",
        "settings",
        "about",
        "shortcuts",
        "show-header",
        "page-layout",
        "audio-player",
        "video-player",
        "zoom-in",
        "zoom-out",
        "zoom-reset",
    )
)


def required(params, name, kind=str):
    value = params.get(name)
    if type(value) is not kind or (kind is str and not value):
        raise ControlError(f"{name} must be a non-empty {kind.__name__}.")
    return value


def dimension(params, name):
    value = required(params, name, int)
    if not 200 <= value <= 4096:
        raise ControlError(f"{name} must be between 200 and 4096 logical pixels.")
    return value


class Automation:
    def __init__(self, app):
        self.app = app
        self.snapshots = {}
        self.serial = 0
        self.widgets = {}
        self.paintables = {}

    def window_removed(self, _app, window):
        if hasattr(window, "webview"):
            identifier = window.webview.identifier
            for mapping in (self.snapshots, self.widgets, self.paintables):
                mapping.pop(identifier, None)

    def dispatch(self, method, params, finish, cancelled):
        def run():
            if cancelled.is_set():
                return GLib.SOURCE_REMOVE
            try:
                if self.app._shutting_down:
                    raise ControlError("Mini Browser is shutting down.")
                self.execute(method, params, finish)
            except ControlError as error:
                finish(error=error)
            except Exception:
                # Never forward exception representations containing page data.
                finish(error="Agent operation failed. Inspect the page and retry.")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)

    def windows(self):
        return {
            window.webview.identifier: window
            for window in self.app.get_windows()
            if hasattr(window, "webview") and not window._closing
        }

    def page(self, params):
        window = self.windows().get(required(params, "pageId", int))
        if window is None:
            raise ControlError("Unknown pageId. Use list_pages to find an open page.")
        return window

    def info(self, window):
        return {
            "pageId": window.webview.identifier,
            "title": window.get_title(),
            "url": window.webview.get_uri() or "",
            "layout": window.stack.get_visible_child_name(),
            "loading": window.webview.loading,
            "ready": window.webview.prepared,
            "width": window.get_width(),
            "height": window.get_height(),
        }

    def execute(self, method, params, finish):
        from .agent_tools import validate

        validate(method, params)
        if method == "quit_browser":
            finish({"quitting": True})
            GLib.timeout_add(100, lambda: self.app.quit() or GLib.SOURCE_REMOVE)
            return
        if method == "list_pages":
            finish({"pages": [self.info(w) for _, w in sorted(self.windows().items())]})
            return
        if method == "new_page":
            url = address_to_uri(required(params, "url")) if "url" in params else None
            finish(self.info(self.app.new_window(url)))
            return
        if method in ("list_home_apps", "add_home_app", "remove_home_app"):
            self.home_app_command(method, params, finish)
            return
        window = self.page(params)
        view = window.webview
        if method == "navigate_page":
            window.load_uri(address_to_uri(required(params, "url")))
            finish(self.info(window))
        elif method == "close_page":
            window.close()
            self.snapshots.pop(view.identifier, None)
            self.paintables.pop(view.identifier, None)
            finish({"closed": True})
        elif method == "activate_action":
            name = required(params, "action")
            action = window.lookup_action(name) if name in ACTIONS else None
            if action is None or not action.get_enabled():
                raise ControlError("Unknown or disabled native action. Use native_snapshot.")
            action.activate(None)
            finish({"activated": name})
        elif method == "resize_page":
            width, height = dimension(params, "width"), dimension(params, "height")
            window.unmaximize()
            window.set_default_size(width, height)
            finish({"requestedWidth": width, "requestedHeight": height})
        elif method == "native_snapshot":
            finish(self.native_snapshot(window))
        elif method == "native_click":
            self.native_click(window, params, finish)
        elif method == "native_fill":
            self.native_fill(window, params, finish)
        elif method == "take_screenshot" and params.get("native", False):
            self.native_screenshot(window, finish)
        else:
            if not view.prepared or view.closed or window.stack.get_visible_child_name() == "home":
                raise ControlError("Page is not ready. Navigate to a website first.")
            self.web_command(window, method, params, finish)

    def home_app_command(self, method, params, finish):
        try:
            if method == "list_home_apps":
                finish({"apps": self.app.library.home_apps()})
                return
            url = home_app_url(params["url"])
            if method == "add_home_app":
                if self.app.deepseek.owns_uri(url):
                    raise ControlError("DeepSeek Harness already has a built-in Home tile.")
                result = self.app.library.add_home_app(url, params.get("title"))
            else:
                result = {"removed": self.app.library.remove_home_app(url)}
            for window in self.windows().values():
                window.refresh_recents()
            finish(result)
        except ValueError as error:
            raise ControlError(str(error)) from error
        except sqlite3.Error as error:
            raise ControlError(
                "Cannot access saved Home apps. Check that the library is writable."
            ) from error

    def command(self, view, method, params, finish, then=None, *, allow_navigation=False):
        if getattr(finish, "cancelled", None) and finish.cancelled.is_set():
            return
        generation = view.generation

        def completed(result, error):
            if getattr(finish, "cancelled", None) and finish.cancelled.is_set():
                return
            if error:
                finish(error="Chromium rejected or timed out the command. Refresh the snapshot.")
            elif view.closed or (view.generation != generation and not allow_navigation):
                finish(error="The document changed during the command. Refresh the snapshot.")
            else:
                try:
                    (then or finish)(result)
                except ControlError as failure:
                    finish(error=failure)
                except Exception:
                    finish(error="Cannot complete the command. Refresh the snapshot.")

        view.command(method, params, completed)

    def web_command(self, window, method, params, finish):
        view = window.webview
        if method == "take_snapshot":
            self.command(
                view,
                "Accessibility.getFullAXTree",
                {},
                finish,
                lambda result: finish(self.snapshot(view, result)),
            )
        elif method in ("click", "fill"):
            self.element(view, method, params, finish)
        elif method == "press_key":
            self.press_key(view, required(params, "key"), finish)
        elif method == "take_screenshot":
            self.command(view, "Page.captureScreenshot", {"format": "png"}, finish)
        elif method == "evaluate_script":
            function = required(params, "function")
            # This is explicitly agent-supplied code, never interpolated credentials.
            self.command(
                view,
                "Runtime.evaluate",
                {
                    "expression": f"({function})()",
                    "awaitPromise": True,
                    "returnByValue": True,
                    "timeout": 10000,
                },
                finish,
                lambda result: self.script_result(result, finish),
            )
        elif method == "cdp_send":
            name = required(params, "method")
            arguments = params.get("params", {})
            if not isinstance(arguments, dict):
                raise ControlError("params must be a JSON object.")
            self.command(view, name, arguments, finish, allow_navigation=True)
        else:
            raise ControlError("Unknown agent command. Use mini-browser agent --help.")

    def snapshot(self, view, result):
        self.serial += 1
        token = f"{view.identifier}_{view.generation}_{self.serial}"
        nodes, targets = [], {}
        source = result.get("nodes", [])
        for node in source[:4000]:
            if node.get("ignored"):
                continue
            record = {
                "nodeId": node["nodeId"],
                "parent": node.get("parentId"),
                "role": node.get("role", {}).get("value", ""),
                "name": node.get("name", {}).get("value", ""),
            }
            if backend := node.get("backendDOMNodeId"):
                uid = f"{token}_{backend}"
                record["uid"] = uid
                targets[uid] = backend
            nodes.append(record)
        self.snapshots[view.identifier] = (view.generation, targets)
        return {
            "pageId": view.identifier,
            "url": view.get_uri(),
            "nodes": nodes,
            "truncated": len(source) > 4000,
        }

    def element(self, view, method, params, finish):
        selector, uid = params.get("selector"), params.get("uid")
        if bool(selector) == bool(uid):
            raise ControlError("Provide exactly one selector or uid from the latest snapshot.")

        def resolved(result):
            object_id = result.get("object", {}).get("objectId")
            if not object_id:
                raise ControlError("Element is unavailable. Refresh the snapshot.")

            def located(result):
                view.command("Runtime.releaseObject", {"objectId": object_id})
                if result.get("exceptionDetails"):
                    raise ControlError("Element is detached, hidden, or cannot accept this input.")
                value = result.get("result", {}).get("value")
                if method == "fill":
                    finish({"filled": True})
                elif isinstance(value, dict):
                    self.mouse(view, value["x"], value["y"], finish)
                else:
                    raise ControlError("Element has no visible bounds.")

            if method == "fill":
                value = params.get("value")
                if not isinstance(value, str):
                    raise ControlError("value must be a string.")
                function = """function(value) {
                    if (!this.isConnected || this.disabled || this.readOnly ||
                        !this.getClientRects().length) throw Error('Unavailable');
                    const proto = this instanceof HTMLInputElement ? HTMLInputElement.prototype :
                        this instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype :
                        this instanceof HTMLSelectElement ? HTMLSelectElement.prototype : null;
                    if (!proto || this.type === 'file') throw Error('Not a text control');
                    this.scrollIntoView({block:'center'}); this.focus();
                    Object.getOwnPropertyDescriptor(proto,'value').set.call(this,value);
                    this.dispatchEvent(new Event('input',{bubbles:true}));
                    this.dispatchEvent(new Event('change',{bubbles:true}));
                    return true;
                }"""
                arguments = [{"value": value}]
            else:
                function = """async function() {
                    if (!this.isConnected) throw Error('Detached');
                    this.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
                    await new Promise(requestAnimationFrame);
                    const r=this.getBoundingClientRect();
                    if (!r.width || !r.height) throw Error('Hidden');
                    const x=Math.max(0,r.left)+Math.min(r.width,innerWidth-Math.max(0,r.left))/2;
                    const y=Math.max(0,r.top)+Math.min(r.height,innerHeight-Math.max(0,r.top))/2;
                    const hit=this.ownerDocument.elementFromPoint(x,y);
                    if (!hit || !(hit===this || this.contains(hit))) throw Error('Covered');
                    return {x,y};
                }"""
                arguments = []
            self.command(
                view,
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": function,
                    "arguments": arguments,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                finish,
                located,
                allow_navigation=method == "fill",
            )

        if uid:
            required(params, "uid")
            generation, targets = self.snapshots.get(view.identifier, (None, {}))
            if generation != view.generation or uid not in targets:
                raise ControlError("Stale or unknown uid. Take a fresh snapshot.")
            self.command(view, "DOM.resolveNode", {"backendNodeId": targets[uid]}, finish, resolved)
        else:
            required(params, "selector")

            def document(result):
                def queried(result):
                    if not result.get("nodeId"):
                        raise ControlError("Selector did not match an element.")
                    self.command(
                        view, "DOM.resolveNode", {"nodeId": result["nodeId"]}, finish, resolved
                    )

                self.command(
                    view,
                    "DOM.querySelector",
                    {
                        "nodeId": result["root"]["nodeId"],
                        "selector": selector,
                    },
                    finish,
                    queried,
                )

            self.command(view, "DOM.getDocument", {}, finish, document)

    def mouse(self, view, x, y, finish):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
            raise ControlError("Invalid element bounds.")

        def released(_result):
            finish({"clicked": True})

        def pressed(_result):
            self.command(
                view,
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
                finish,
                released,
                allow_navigation=True,
            )

        self.command(
            view,
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1,
            },
            finish,
            pressed,
            allow_navigation=True,
        )

    def press_key(self, view, key, finish):
        keys = {
            "Enter": (13, "Enter"),
            "Tab": (9, "Tab"),
            "Escape": (27, "Escape"),
            "Backspace": (8, "Backspace"),
            "Delete": (46, "Delete"),
            "ArrowLeft": (37, "ArrowLeft"),
            "ArrowUp": (38, "ArrowUp"),
            "ArrowRight": (39, "ArrowRight"),
            "ArrowDown": (40, "ArrowDown"),
        }
        pieces = key.split("+")
        modifiers = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
        if any(piece not in modifiers for piece in pieces[:-1]):
            raise ControlError("Use Control, Alt, Meta or Shift modifiers.")
        name = pieces[-1]
        if name not in keys and (len(name) != 1 or not name.isascii() or not name.isalnum()):
            raise ControlError("Unsupported key. Use Enter, Tab, Escape, arrows or ASCII letters.")
        code, physical = keys.get(
            name,
            (
                ord(name.upper()) if len(name) == 1 else 0,
                f"Digit{name}" if name.isdigit() else f"Key{name.upper()}",
            ),
        )
        modifier_bits = sum({modifiers[p] for p in pieces[:-1]})
        character = name.upper() if modifier_bits & 8 else name
        data = {
            "key": character,
            "code": physical,
            "windowsVirtualKeyCode": code,
            "modifiers": modifier_bits,
        }
        if name == "Enter":
            data["text"] = "\r"
        elif len(name) == 1 and not modifier_bits & 7:
            data["text"] = character
        self.command(
            view,
            "Input.dispatchKeyEvent",
            {"type": "keyDown", **data},
            finish,
            lambda _: self.command(
                view,
                "Input.dispatchKeyEvent",
                {"type": "keyUp", **data},
                finish,
                allow_navigation=True,
            ),
            allow_navigation=True,
        )

    def script_result(self, result, finish):
        if result.get("exceptionDetails"):
            finish(error="Page script failed. Inspect the page before retrying.")
        else:
            finish({"value": result.get("result", {}).get("value")})

    def check_native(self, window):
        if any(getattr(dialog, "_password_dialog", False) for dialog in window.get_dialogs()):
            raise ControlError("Close the password dialog before inspecting native UI.")

    def native_snapshot(self, window):
        self.check_native(window)
        self.serial += 1
        targets, nodes = {}, []

        def visit(widget, parent=None):
            if len(nodes) >= 2000 or not widget.get_mapped():
                return
            uid = f"gtk_{self.serial}_{len(nodes)}"
            targets[uid] = widget
            record = {
                "uid": uid,
                "parent": parent,
                "type": widget.__gtype__.name,
                "sensitive": widget.get_sensitive(),
                "cssClasses": list(widget.get_css_classes()),
            }
            # Never export editable contents or password-entry internals.
            if isinstance(widget, Gtk.PasswordEntry):
                record["redacted"] = True
                nodes.append(record)
                return
            for key, getter in (
                ("label", "get_label"),
                ("tooltip", "get_tooltip_text"),
                ("icon", "get_icon_name"),
                ("action", "get_action_name"),
                ("placeholder", "get_placeholder_text"),
            ):
                # Gtk.Entry's icon getter requires an icon position argument.
                if key == "icon" and isinstance(widget, Gtk.Entry):
                    continue
                if hasattr(widget, getter):
                    value = getattr(widget, getter)()
                    if isinstance(value, str) and value:
                        record[key] = value
            nodes.append(record)
            child = widget.get_first_child()
            while child:
                visit(child, uid)
                child = child.get_next_sibling()

        visit(window)
        self.widgets[window.webview.identifier] = targets
        return {
            **self.info(window),
            "widgets": nodes,
            "actions": {
                name: window.lookup_action(name).get_enabled()
                for name in sorted(ACTIONS)
                if window.lookup_action(name) is not None
            },
        }

    def native_click(self, window, params, finish):
        self.check_native(window)
        widget = self.widgets.get(window.webview.identifier, {}).get(required(params, "uid"))
        if not isinstance(widget, (Gtk.Button, Gtk.MenuButton)) or not widget.get_mapped():
            raise ControlError("Stale or unsupported widget. Take a fresh native_snapshot.")
        if not widget.is_sensitive():
            raise ControlError("Native control is disabled.")
        if isinstance(widget, Gtk.MenuButton):
            widget.popup()
        else:
            widget.emit("clicked")
        finish({"clicked": True})

    def native_fill(self, window, params, finish):
        self.check_native(window)
        widget = self.widgets.get(window.webview.identifier, {}).get(required(params, "uid"))
        if (
            not isinstance(widget, (Gtk.Entry, Gtk.Text, Gtk.SearchEntry))
            or not widget.get_mapped()
            or not widget.is_sensitive()
            or not widget.get_editable()
        ):
            raise ControlError("Stale or unsupported text field. Take a fresh native_snapshot.")
        ancestor = widget
        while ancestor:
            if isinstance(ancestor, Gtk.PasswordEntry) or (
                isinstance(ancestor, (Gtk.Entry, Gtk.Text)) and not ancestor.get_visibility()
            ):
                raise ControlError("Native password fields cannot be controlled by agents.")
            ancestor = ancestor.get_parent()
        widget.grab_focus()
        widget.set_text(params["value"])
        if params.get("submit", False):
            widget.emit("activate")
        finish({"filled": True})

    def native_screenshot(self, window, finish):
        self.check_native(window)
        paintable = self.paintables.setdefault(
            window.webview.identifier, Gtk.WidgetPaintable.new(window)
        )

        def capture():
            try:
                self.check_native(window)
                snapshot = Gtk.Snapshot()
                paintable.snapshot(snapshot, window.get_width(), window.get_height())
                node = snapshot.to_node()
                if node is None or window._closing:
                    raise ControlError("Window has not painted yet. Retry the screenshot.")
                texture = window.get_renderer().render_texture(node, None)
                finish({"data": base64.b64encode(texture.save_to_png_bytes().get_data()).decode()})
            except Exception:
                finish(error="Cannot capture this window. Close dialogs and retry.")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, capture)
