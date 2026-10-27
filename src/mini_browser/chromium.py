"""Sandboxed Chromium embedded in GTK, with an in-process, typed script channel.

CEF owns networking and the page processes. GTK owns every window and dialog.
No debugging socket is opened. Script results and credentials are never logged.
"""

import ctypes
import json
import math
import os
import re
import sysconfig
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

PAINT = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
EVENT = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)
RESULT = ctypes.CFUNCTYPE(
    None, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t
)


def engine_error(message="The page operation could not be completed."):
    return GLib.Error(message, "mini-browser-chromium", 1)


def runtime_path():
    override = os.environ.get("MINI_BROWSER_CEF_RUNTIME")
    if override:
        return Path(override).resolve()
    package = Path(__file__).resolve().parent.parent
    for candidate in (
        package / "cef",
        package.parent / "build/cef/runtime",
        Path(sysconfig.get_config_var("LIBDIR") or "/usr/lib64") / "mini-browser/cef",
    ):
        if (candidate / "libmini_cef.so").is_file():
            return candidate.resolve()
    raise ImportError(
        "Chromium runtime missing. Run ./scripts/build-cef, then restart Mini Browser."
    )


class Bridge:
    def __init__(self):
        self.runtime = runtime_path()
        try:
            self.native = ctypes.CDLL(str(self.runtime / "libmini_cef.so"))
        except OSError as error:
            raise ImportError(
                "Could not load Chromium. Rebuild with ./scripts/build-cef."
            ) from error
        signatures = {
            "abi_version": ([], ctypes.c_int),
            "version": ([], ctypes.c_char_p),
            "initialize": (
                [ctypes.c_char_p] * 3 + [ctypes.c_int] * 2 + [PAINT, EVENT, RESULT],
                ctypes.c_int,
            ),
            "pump": ([], None),
            "create": ([ctypes.c_int], None),
            "close": ([ctypes.c_int], None),
            "resize": ([ctypes.c_int] * 3, None),
            "load": ([ctypes.c_int, ctypes.c_char_p], None),
            "stop": ([ctypes.c_int], None),
            "reload": ([ctypes.c_int] * 2, None),
            "focus": ([ctypes.c_int] * 2, None),
            "mute": ([ctypes.c_int] * 2, None),
            "zoom": ([ctypes.c_int, ctypes.c_double], None),
            "frame_rate": ([ctypes.c_int] * 2, None),
            "mouse": ([ctypes.c_int] * 6, None),
            "wheel": ([ctypes.c_int] * 5, None),
            "key": ([ctypes.c_int] * 5, None),
            "text": ([ctypes.c_int, ctypes.c_char_p], None),
            "preedit": ([ctypes.c_int, ctypes.c_char_p, ctypes.c_int], None),
            "command": ([ctypes.c_int] * 2 + [ctypes.c_char_p] * 2, ctypes.c_int),
            "download": ([ctypes.c_int, ctypes.c_char_p], None),
            "cancel_download": ([ctypes.c_int], None),
            "reply": ([ctypes.c_int] * 2 + [ctypes.c_char_p], None),
            "shutdown": ([], ctypes.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.native, "mini_cef_" + name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)
        if self.abi_version() != 3:
            raise ImportError("Chromium bridge version mismatch. Run ./scripts/build-cef again.")


class Session(GObject.Object):
    __gsignals__ = {"download-started": (GObject.SignalFlags.RUN_LAST, None, (object,))}

    def __init__(self, profile, cache, *, allow_autoplay=False, silent=False):
        super().__init__()
        self.bridge = Bridge()
        self.views, self.pending, self.downloads = {}, {}, {}
        self.next_view = self.next_message = 0
        self.closed = False
        for directory in (profile, cache):
            Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.paint_callback = PAINT(self._paint)
        self.event_callback = EVENT(self._event)
        self.result_callback = RESULT(self._result)
        if not self.bridge.initialize(
            os.fsencode(self.bridge.runtime),
            os.fsencode(profile),
            os.fsencode(cache),
            allow_autoplay,
            silent,
            self.paint_callback,
            self.event_callback,
            self.result_callback,
        ):
            raise RuntimeError(
                "Chromium could not start. Check the runtime and Linux namespace sandbox."
            )
        self.source = GLib.timeout_add(10, self._pump)

    def _pump(self):
        self.bridge.pump()
        expired = [
            key for key, (_, deadline) in self.pending.items() if deadline < time.monotonic()
        ]
        for key in expired:
            callback, _ = self.pending.pop(key)
            callback(None, engine_error("The page operation timed out."))
        return GLib.SOURCE_CONTINUE

    def _paint(self, identifier, pointer, width, height):
        view = self.views.get(identifier)
        if view and not view.closed and 0 < width <= 4096 and 0 < height <= 4096:
            view.pending_frame = (ctypes.string_at(pointer, width * height * 4), width, height)
            if not view.frame_source:
                view.frame_source = GLib.idle_add(view._present_frame)

    def _event(self, identifier, kind, data):
        # Authentication may arrive on CEF's IO thread. Every GTK mutation runs
        # on the main thread, outside CEF's callback stack.
        GLib.idle_add(self._dispatch, identifier, kind.decode(), json.loads(data))

    def _dispatch(self, identifier, kind, data):
        if kind == "download":
            download = Download(self, data["id"])
            self.downloads[data["id"]] = download
            self.emit("download-started", download)
            download.emit("decide-destination", data["name"])
        elif kind == "download-update":
            download = self.downloads.get(data["id"])
            if download and (data["complete"] or data["cancelled"] or not data["progress"]):
                download.emit(
                    "finished" if data["complete"] else "failed",
                    *([] if data["complete"] else [engine_error()]),
                )
                self.downloads.pop(data["id"], None)
        view = self.views.get(identifier)
        if view and not view.closed and kind not in ("download", "download-update"):
            view._event(kind, data)
        if kind == "closed":
            self.views.pop(identifier, None)
        return GLib.SOURCE_REMOVE

    def _result(self, identifier, message, success, pointer, size):
        data = ctypes.string_at(pointer, size) if pointer and size else b"{}"
        GLib.idle_add(self._complete, identifier, message, success, data)

    def _complete(self, identifier, message, success, data):
        entry = self.pending.pop((identifier, message), None)
        if entry:
            callback, _ = entry
            callback(json.loads(data) if success else None, None if success else engine_error())
        return GLib.SOURCE_REMOVE

    def command(self, view, method, parameters=None, callback=None):
        self.next_message += 1
        message = self.next_message
        if callback:
            self.pending[view.identifier, message] = (callback, time.monotonic() + 15)
        accepted = self.bridge.command(
            view.identifier, message, method.encode(), json.dumps(parameters or {}).encode()
        )
        if not accepted and callback:
            self.pending.pop((view.identifier, message), None)
            GLib.idle_add(callback, None, engine_error())

    def close(self):
        if self.closed:
            return
        for view in tuple(self.views.values()):
            view.close()
        GLib.source_remove(self.source)
        # Close callbacks are drained by the native shutdown before CefShutdown.
        if not self.bridge.shutdown():
            raise RuntimeError("Chromium did not stop within five seconds.")
        self.closed = True
        self.pending.clear()


class LoadEvent(IntEnum):
    STARTED = 0
    FINISHED = 1


class UserContentInjectedFrames(IntEnum):
    ALL_FRAMES = 0
    TOP_FRAME = 1


class UserScriptInjectionTime(IntEnum):
    START = 0


@dataclass
class UserScript:
    source: str
    frames: int
    world: str

    @classmethod
    def new_for_world(cls, source, frames, _time, world, _allow, _block):
        return cls(source, frames, world)


class UserContent:
    def __init__(self, view):
        self.view, self.scripts = view, []

    def add_script(self, script):
        self.scripts.append(script)
        if self.view.prepared:
            self.install(script)

    def install(self, script, callback=None):
        source = script.source
        if script.frames == UserContentInjectedFrames.TOP_FRAME:
            source = "if (window === window.top) {\n" + source + "\n}"
        self.view.command(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": source,
                "worldName": script.world,
                "runImmediately": True,
            },
            callback,
        )


@dataclass
class HistoryItem:
    identifier: int
    uri: str
    title: str

    def get_uri(self):
        return self.uri

    def get_title(self):
        return self.title


class History(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, (object, object))}

    def __init__(self):
        super().__init__()
        self.entries, self.index = [], -1

    def update(self, data):
        old = {item.identifier: item for item in self.entries}
        selected = data.get("currentIndex", -1)
        current = data.get("entries", [])
        current_id = current[selected]["id"] if 0 <= selected < len(current) else None
        entries = []
        for record in current:
            if not record["url"].startswith(("https://", "http://")):
                continue
            item = old.get(record["id"]) or HistoryItem(record["id"], record["url"], "")
            item.uri, item.title = record["url"], record.get("title", "")
            entries.append(item)
        self.entries = entries
        self.index = next(
            (i for i, item in enumerate(entries) if item.identifier == current_id), -1
        )
        self.emit("changed", None, None)

    def get_length(self):
        return len(self.entries)

    def get_nth_item(self, offset):
        index = self.index + offset
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def get_back_item(self):
        return self.get_nth_item(-1)

    def get_back_list_with_limit(self, limit):
        return list(reversed(self.entries[max(0, self.index - limit) : self.index]))


class JavaScriptValue:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def to_string(self):
        return self.value if isinstance(self.value, str) else json.dumps(self.value)

    def to_json(self, _indent=0):
        return json.dumps(self.value)


class View(Gtk.Widget):
    __gtype_name__ = "MiniBrowserChromiumView"
    __gsignals__ = {
        "load-changed": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        "load-failed": (GObject.SignalFlags.RUN_LAST, bool, (int, str, object)),
        "load-failed-with-tls-errors": (GObject.SignalFlags.RUN_LAST, bool, (str, object, int)),
        "web-process-terminated": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        "close": (GObject.SignalFlags.RUN_LAST, None, ()),
        "unsupported-uri": (GObject.SignalFlags.RUN_LAST, None, ()),
    }
    title = GObject.Property(type=str, default="")
    uri = GObject.Property(type=str, default="")
    is_muted = GObject.Property(type=bool, default=False)
    playing_audio = GObject.Property(type=bool, default=False)

    def __init__(self, *, network_session):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.session, self.bridge = network_session, network_session.bridge
        self.session.next_view += 1
        self.identifier = self.session.next_view
        self.session.views[self.identifier] = self
        self.manager, self.history = UserContent(self), History()
        self.closed = self.prepared = self.ready = self.loading = False
        self.pending_uri = None
        self.generation = 0
        self.contexts, self.audio_contexts = {}, {}
        self.texture = self.pending_frame = None
        self.frame_source = 0
        self.zoom_factor = 1.0
        self.position = (0, 0)
        self.dialogs = set()
        self._media_busy = False
        self._media_source = GLib.timeout_add(400, self._poll_audio)
        self._input()
        self.bridge.create(self.identifier)

    def command(self, method, parameters=None, callback=None):
        self.session.command(self, method, parameters, callback)

    def _prepare(self):
        operations = [("Page.enable", {}), ("Runtime.enable", {}), ("WebAudio.enable", {})]
        operations.extend(
            (
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": ("if(window===window.top){\n" + s.source + "\n}")
                    if s.frames == UserContentInjectedFrames.TOP_FRAME
                    else s.source,
                    "worldName": s.world,
                    "runImmediately": True,
                },
            )
            for s in self.manager.scripts
        )

        def step(_data=None, error=None):
            if self.closed:
                return
            if error:
                self.emit("web-process-terminated", 0)
            elif operations:
                method, parameters = operations.pop(0)
                self.command(method, parameters, step)
            else:
                self.prepared = True
                if self.pending_uri:
                    uri, self.pending_uri = self.pending_uri, None
                    self.load_uri(uri)

        step()

    def _event(self, kind, data):
        if kind == "ready":
            self.ready = True
            self.bridge.resize(self.identifier, max(1, self.get_width()), max(1, self.get_height()))
            self._prepare()
        elif kind == "uri" and self.prepared and data["uri"] != "about:blank":
            self.uri = data["uri"]
            self._history()
        elif kind == "title" and self.prepared:
            self.title = data["title"]
        elif kind == "loading" and self.prepared:
            if not data["loading"] and self.loading:
                self._history(self._finish_load)
        elif kind == "load-start" and self.prepared:
            self._start_load()
        elif kind in ("tls-error", "load-error") and self.prepared:
            self.loading = False
            if kind == "tls-error":
                self.emit("load-failed-with-tls-errors", data["uri"], None, data["code"])
            else:
                self.emit("load-failed", LoadEvent.STARTED, data["uri"], engine_error())
        elif kind == "terminated":
            self.loading = False
            self.contexts.clear()
            self.emit("web-process-terminated", 0)
        elif kind == "unsupported-uri":
            self.emit("unsupported-uri")
        elif kind == "Runtime.executionContextCreated":
            context = data["context"]
            self.contexts[context["id"]] = context
        elif kind == "Runtime.executionContextsCleared":
            self.contexts.clear()
            self.audio_contexts.clear()
        elif kind == "Runtime.executionContextDestroyed":
            self.contexts.pop(data.get("executionContextId"), None)
        elif kind in ("Page.navigatedWithinDocument", "Page.frameNavigated"):
            frame = data.get("frame", {})
            if not frame.get("parentId") and data.get("url", frame.get("url", "")).startswith(
                ("http://", "https://")
            ):
                self._history()
        elif kind.startswith("WebAudio.context"):
            if "context" in data:
                context = data["context"]
                self.audio_contexts[context["contextId"]] = context.get("contextState") == "running"
            else:
                self.audio_contexts.pop(data.get("contextId"), None)
        elif kind in ("authenticate", "javascript-dialog", "file-dialog"):
            self._dialog(kind, data)
        elif kind == "context-menu":
            self._context_menu(data)
        elif kind == "caret":
            rect = Gdk.Rectangle()
            rect.x, rect.y = data["x"], data["y"]
            rect.width, rect.height = data["width"], data["height"]
            self.im.set_cursor_location(rect)

    def _start_load(self):
        if self.loading:
            return
        self.loading = True
        self.generation += 1
        self.audio_contexts.clear()
        self._set_audio(False)
        self.emit("load-changed", LoadEvent.STARTED)

    def _finish_load(self):
        if not self.closed and self.loading:
            self.loading = False
            self.emit("load-changed", LoadEvent.FINISHED)

    def _history(self, completed=None):
        def done(data, error):
            if not self.closed and not error:
                self.history.update(data)
                item = self.history.get_nth_item(0)
                if item and item.uri != self.uri:
                    self.uri = item.uri
            if completed:
                completed()

        self.command("Page.getNavigationHistory", callback=done)

    def load_uri(self, uri):
        if self.closed:
            return
        if not self.prepared:
            self.pending_uri = uri
            return
        self._start_load()
        self.bridge.load(self.identifier, uri.encode())

    def reload(self):
        self._start_load()
        self.bridge.reload(self.identifier, 0)

    def reload_bypass_cache(self):
        self._start_load()
        self.bridge.reload(self.identifier, 1)

    def stop_loading(self):
        self.pending_uri = None
        self.bridge.stop(self.identifier)

    def get_uri(self):
        return self.pending_uri or self.uri

    def get_title(self):
        return self.title

    def is_loading(self):
        return self.loading or bool(self.pending_uri)

    def get_user_content_manager(self):
        return self.manager

    def get_back_forward_list(self):
        return self.history

    def can_go_back(self):
        return self.history.index > 0

    def can_go_forward(self):
        return 0 <= self.history.index < len(self.history.entries) - 1

    def go_back(self):
        self.go_to_back_forward_list_item(self.history.get_nth_item(-1))

    def go_forward(self):
        self.go_to_back_forward_list_item(self.history.get_nth_item(1))

    def go_to_back_forward_list_item(self, item):
        if item and any(entry is item for entry in self.history.entries):
            self.command("Page.navigateToHistoryEntry", {"entryId": item.identifier})

    def get_zoom_level(self):
        return self.zoom_factor

    def set_zoom_level(self, factor):
        self.zoom_factor = factor
        self.bridge.zoom(self.identifier, math.log(factor, 1.2))

    def get_is_muted(self):
        return self.is_muted

    def set_is_muted(self, muted):
        self.is_muted = muted
        self.bridge.mute(self.identifier, muted)

    def is_playing_audio(self):
        return self.playing_audio

    def _set_audio(self, playing):
        if self.playing_audio != playing:
            self.playing_audio = playing

    def _poll_audio(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if not self.prepared or self.loading or self._media_busy or not self.uri:
            return GLib.SOURCE_CONTINUE
        self._media_busy = True

        def done(_view, result):
            self._media_busy = False
            if not result.error:
                self._set_audio(bool(result.value or any(self.audio_contexts.values())))

        self.evaluate_javascript(
            "[...document.querySelectorAll('audio,video')].some(m=>!m.paused&&!m.ended&&!m.muted&&m.volume>0&&m.readyState>=2)",
            -1,
            None,
            None,
            None,
            done,
        )
        return GLib.SOURCE_CONTINUE

    def _script(self, source, arguments, world, cancellable, callback, *, expression=False):
        generation = self.generation
        completed = False

        def stale():
            return (
                self.closed
                or generation != self.generation
                or (cancellable and cancellable.is_cancelled())
            )

        def finish(data=None, error=None):
            nonlocal completed
            if completed:
                return
            completed = True
            if callback:
                remote = (data or {}).get("result", {})
                if stale() or (data or {}).get("exceptionDetails"):
                    error = engine_error()
                callback(self, JavaScriptValue(remote.get("value"), error))

        if not self.prepared or stale():
            GLib.idle_add(finish, None, engine_error())
            return
        if expression and not world:
            self.command(
                "Runtime.evaluate",
                {
                    "expression": source,
                    "returnByValue": True,
                    "awaitPromise": True,
                    "userGesture": True,
                },
                finish,
            )
            return

        def invoke(context):
            if stale():
                finish(error=engine_error())
                return
            unique = context.get("uniqueId")
            if not unique:
                finish(error=engine_error())
                return
            # Argument NAMES are validated. Values, including credentials, travel
            # as typed protocol arguments, never as JavaScript source text.
            names = list(arguments)
            if any(not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) for name in names):
                finish(error=engine_error())
                return
            body = "return (" + source + ");" if expression else source
            declaration = "async function(" + ",".join(names) + "){\n" + body + "\n}"
            self.command(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": declaration,
                    "uniqueContextId": unique,
                    "arguments": [{"value": value} for value in arguments.values()],
                    "returnByValue": True,
                    "awaitPromise": True,
                    "userGesture": True,
                },
                finish,
            )

        def tree_ready(data, error):
            if error or stale():
                finish(error=error or engine_error())
                return
            frame = data["frameTree"]["frame"]["id"]
            if world:

                def created(data, error):
                    if error:
                        finish(error=error)
                    else:
                        invoke(self.contexts.get(data["executionContextId"], {}))

                self.command(
                    "Page.createIsolatedWorld", {"frameId": frame, "worldName": world}, created
                )
            else:
                context = next(
                    (
                        c
                        for c in self.contexts.values()
                        if c.get("auxData", {}).get("frameId") == frame
                        and c.get("auxData", {}).get("isDefault")
                    ),
                    {},
                )
                invoke(context)

        self.command("Page.getFrameTree", callback=tree_ready)

    def evaluate_javascript(
        self, source, _length=-1, world=None, _uri=None, cancellable=None, callback=None, *_data
    ):
        self._script(source, {}, world, cancellable, callback, expression=True)

    def call_async_javascript_function(
        self, source, _length, arguments, world, _uri, cancellable, callback
    ):
        self._script(source, arguments.unpack() if arguments else {}, world, cancellable, callback)

    @staticmethod
    def evaluate_javascript_finish(result):
        if result.error:
            raise result.error
        return result

    call_async_javascript_function_finish = evaluate_javascript_finish

    def do_size_allocate(self, width, height, _baseline):
        self.bridge.resize(self.identifier, width, height)

    def do_snapshot(self, snapshot):
        if self.texture:
            snapshot.append_texture(
                self.texture, Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
            )

    def _present_frame(self):
        self.frame_source = 0
        frame, self.pending_frame = self.pending_frame, None
        if frame and not self.closed:
            pixels, width, height = frame
            self.texture = Gdk.MemoryTexture.new(
                width,
                height,
                Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                GLib.Bytes.new(pixels),
                width * 4,
            )
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _input(self):
        self.im = Gtk.IMMulticontext()
        self.im.set_client_widget(self)
        self.im.connect(
            "commit", lambda _im, text: self.bridge.text(self.identifier, text.encode())
        )
        self.im.connect("preedit-changed", self._preedit)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)
        clicks = Gtk.GestureClick(button=0)
        clicks.connect("pressed", self._press)
        clicks.connect("released", self._release)
        self.add_controller(clicks)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        keyboard = Gtk.EventControllerKey()
        keyboard.connect("key-pressed", self._key_pressed)
        keyboard.connect("key-released", self._key_released)
        self.add_controller(keyboard)
        focus = Gtk.EventControllerFocus()
        focus.connect(
            "enter", lambda *_: (self.im.focus_in(), self.bridge.focus(self.identifier, 1))
        )
        focus.connect(
            "leave", lambda *_: (self.im.focus_out(), self.bridge.focus(self.identifier, 0))
        )
        self.add_controller(focus)

    def _preedit(self, im):
        text, _attributes, cursor = im.get_preedit_string()
        cursor = len(text[:cursor].encode("utf-16-le")) // 2
        self.bridge.preedit(self.identifier, text.encode(), cursor)

    def _motion(self, _controller, x, y):
        self.position = (int(x), int(y))
        self.bridge.mouse(self.identifier, *self.position, 0, 0, 0)

    def _press(self, gesture, count, x, y):
        self.grab_focus()
        self.bridge.mouse(
            self.identifier, int(x), int(y), gesture.get_current_button(), 0, min(count, 2)
        )

    def _release(self, gesture, count, x, y):
        self.bridge.mouse(
            self.identifier, int(x), int(y), gesture.get_current_button(), 1, min(count, 2)
        )

    def _scroll(self, _controller, dx, dy):
        self.bridge.wheel(self.identifier, *self.position, int(-dx * 60), int(-dy * 60))
        return True

    @staticmethod
    def _key_code(value):
        special = {
            Gdk.KEY_BackSpace: 8,
            Gdk.KEY_Tab: 9,
            Gdk.KEY_Return: 13,
            Gdk.KEY_Escape: 27,
            Gdk.KEY_Left: 37,
            Gdk.KEY_Up: 38,
            Gdk.KEY_Right: 39,
            Gdk.KEY_Down: 40,
            Gdk.KEY_Delete: 46,
            Gdk.KEY_Home: 36,
            Gdk.KEY_End: 35,
            Gdk.KEY_Page_Up: 33,
            Gdk.KEY_Page_Down: 34,
        }
        return special.get(value, Gdk.keyval_to_unicode(Gdk.keyval_to_upper(value)))

    @staticmethod
    def _modifiers(state):
        return (
            (2 if state & Gdk.ModifierType.SHIFT_MASK else 0)
            | (4 if state & Gdk.ModifierType.CONTROL_MASK else 0)
            | (8 if state & Gdk.ModifierType.ALT_MASK else 0)
        )

    @staticmethod
    def _host_shortcut(value, state):
        lower = Gdk.keyval_to_lower(value)
        return (
            value in (Gdk.KEY_F1, Gdk.KEY_F5, Gdk.KEY_F10, Gdk.KEY_Escape)
            or state & Gdk.ModifierType.ALT_MASK
            and value in (Gdk.KEY_Home, Gdk.KEY_Left, Gdk.KEY_Right)
            or state & Gdk.ModifierType.CONTROL_MASK
            and lower
            in (
                Gdk.KEY_n,
                Gdk.KEY_q,
                Gdk.KEY_l,
                Gdk.KEY_w,
                Gdk.KEY_r,
                Gdk.KEY_b,
                Gdk.KEY_h,
                Gdk.KEY_d,
                Gdk.KEY_p,
                Gdk.KEY_i,
                Gdk.KEY_k,
                Gdk.KEY_comma,
                Gdk.KEY_plus,
                Gdk.KEY_equal,
                Gdk.KEY_minus,
                Gdk.KEY_0,
                Gdk.KEY_question,
            )
        )

    def _key_pressed(self, controller, value, code, state):
        if self._host_shortcut(value, state):
            return False
        if state & Gdk.ModifierType.CONTROL_MASK:
            operation = {Gdk.KEY_c: "copy", Gdk.KEY_x: "cut", Gdk.KEY_v: "paste"}.get(
                Gdk.keyval_to_lower(value)
            )
            if operation:
                self._clipboard(operation)
                return True
        self.bridge.key(self.identifier, self._key_code(value), code, self._modifiers(state), 0)
        event = controller.get_current_event()
        if event:
            self.im.filter_keypress(event)
        return True

    def _key_released(self, controller, value, code, state):
        if not self._host_shortcut(value, state):
            self.bridge.key(self.identifier, self._key_code(value), code, self._modifiers(state), 1)
            event = controller.get_current_event()
            if event:
                self.im.filter_keypress(event)

    def _clipboard(self, operation):
        clipboard = self.get_clipboard()
        if operation == "paste":
            generation = self.generation

            def pasted(clipboard, result):
                try:
                    text = clipboard.read_text_finish(result)
                    if text and not self.closed and generation == self.generation:
                        self.command("Input.insertText", {"text": text})
                except GLib.Error:
                    pass

            clipboard.read_text_async(None, pasted)
        else:

            def copied(_view, result):
                if not result.error and isinstance(result.value, str) and result.value:
                    clipboard.set(result.value)
                    if operation == "cut":
                        self.evaluate_javascript("document.execCommand('delete')", -1)

            self.evaluate_javascript(
                "(()=>{const e=document.activeElement;"
                "if(e?.type==='password')return '';"
                "return e&&typeof e.selectionStart==='number'?"
                "e.value.slice(e.selectionStart,e.selectionEnd):String(getSelection())})()",
                -1,
                None,
                None,
                None,
                copied,
            )

    def _context_menu(self, data):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, action in (("Copy", "copy"), ("Cut", "cut"), ("Paste", "paste")):
            button = Gtk.Button(label=label, css_classes=["flat"])
            button.connect(
                "clicked", lambda _b, action=action: (self._clipboard(action), popover.popdown())
            )
            box.append(button)
        popover.set_child(box)
        popover.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = data["x"], data["y"], 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda *_: popover.unparent())
        popover.popup()

    def _dialog(self, kind, data):
        identifier = data["request"]
        parent = self.get_root()
        if kind == "file-dialog":
            picker = Gtk.FileDialog(title=data["title"] or "Select a file")
            mode = data["mode"] & 0xFF
            method = "open_multiple" if mode == 1 else "select_folder" if mode == 2 else "open"

            def selected(picker, result):
                try:
                    chosen = getattr(picker, method + "_finish")(result)
                    files = list(chosen) if method == "open_multiple" else [chosen]
                    paths = [file.get_path() for file in files if file.get_path()]
                    self.bridge.reply(
                        identifier, bool(paths), json.dumps({"paths": paths}).encode()
                    )
                except GLib.Error:
                    self.bridge.reply(identifier, 0, b"{}")

            getattr(picker, method)(parent, None, selected)
            return
        auth = kind == "authenticate"
        dialog = Adw.AlertDialog(
            heading="Proxy sign in"
            if auth and data["proxy"]
            else "Website sign in"
            if auth
            else "Website message",
            body=(data["host"] if auth else data["message"]),
            body_use_markup=False,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        username, password, prompt = Gtk.Entry(), Gtk.PasswordEntry(), Gtk.Entry()
        if auth:
            username.set_placeholder_text("Username")
            password.set_property("placeholder-text", "Password")
            box.append(username)
            box.append(password)
        elif data["type"] == 2:
            prompt.set_text(data["prompt"])
            box.append(prompt)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Sign in" if auth else "OK")
        dialog.set_close_response("cancel")
        self.dialogs.add(dialog)

        def response(_dialog, answer):
            values = (
                {"username": username.get_text(), "password": password.get_text()}
                if auth
                else {"text": prompt.get_text()}
            )
            self.bridge.reply(identifier, answer == "ok", json.dumps(values).encode())
            password.set_text("")
            self.dialogs.discard(dialog)

        dialog.connect("response", response)
        dialog.present(parent)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._set_audio(False)
        self.loading = False
        self.generation += 1
        GLib.source_remove(self._media_source)
        if self.frame_source:
            GLib.source_remove(self.frame_source)
            self.frame_source = 0
        for dialog in tuple(self.dialogs):
            dialog.force_close()
        self.pending_frame = self.texture = None
        self.im.set_client_widget(None)
        self.bridge.close(self.identifier)


class Download(GObject.Object):
    __gsignals__ = {
        "decide-destination": (GObject.SignalFlags.RUN_LAST, bool, (str,)),
        "finished": (GObject.SignalFlags.RUN_LAST, None, ()),
        "failed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, session, identifier):
        super().__init__()
        self.session, self.identifier = session, identifier

    def set_allow_overwrite(self, _allowed):
        pass  # Replacement is confirmed by the native save dialog.

    def set_destination(self, uri):
        path = Gio.File.new_for_uri(uri).get_path()
        if path:
            self.session.bridge.download(self.identifier, os.fsencode(path))
        else:
            self.cancel()

    def cancel(self):
        self.session.bridge.cancel_download(self.identifier)
