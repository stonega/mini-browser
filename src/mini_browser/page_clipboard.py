"""Bridge explicit text and image clipboard actions between Chromium and GTK."""

import base64
import ipaddress
import json
import struct
import time
from urllib.parse import urlsplit

WORLD = "mini-browser-clipboard"
BINDING = "miniBrowserWriteClipboard"
LIMIT = 1024 * 1024
IMAGE_LIMIT = 16 * 1024 * 1024
PIXEL_LIMIT = 32 * 1024 * 1024
PAYLOAD_LIMIT = (IMAGE_LIMIT + 2) // 3 * 4 + LIMIT * 6 + 1024

# Chromium performs its own Clipboard API checks first. The isolated world
# verifies user activation before forwarding a write. Desktop clipboard reads
# require a native Paste action. Messages carry data, never executable source.
MAIN_SCRIPT = r"""
(() => {
    if (window !== top) return;
    const pending = new Map();
    let next = 0;
    const denied = () => new DOMException('Clipboard copy requires an active page and user action.',
                                         'NotAllowedError');
    addEventListener('mini-browser-clipboard-response', event => {
        if (typeof event.detail !== 'string') return;
        let reply;
        try { reply = JSON.parse(event.detail); } catch { return; }
        const request = pending.get(reply.id);
        if (!request) return;
        pending.delete(reply.id);
        clearTimeout(request.timer);
        if (reply.error) request.reject(denied()); else request.resolve();
    });
    const forward = content => {
        if (pending.size >= 8) return Promise.reject(denied());
        return new Promise((resolve, reject) => {
            const id = ++next;
            const timer = setTimeout(() => {
                pending.delete(id); reject(denied());
            }, 5000);
            pending.set(id, {resolve, reject, timer});
            dispatchEvent(new CustomEvent('mini-browser-clipboard-request', {
                detail: JSON.stringify({id, ...content})
            }));
        });
    };
    if (isSecureContext && navigator.clipboard) {
        const write = Clipboard.prototype.write;
        Object.defineProperty(Clipboard.prototype, 'writeText', {
            configurable: true, writable: true,
            value: async function(text) {
                if (this !== navigator.clipboard) throw new TypeError('Illegal invocation');
                text = String(text);
                if (text.length > 1048576) throw denied();
                return forward({text});
            }
        });
        Object.defineProperty(Clipboard.prototype, 'write', {
            configurable: true, writable: true,
            value: async function(items) {
                items = Array.from(items);
                await write.call(this, items);
                const item = items[0], content = {};
                if (item?.types.includes('text/plain')) {
                    const text = await item.getType('text/plain');
                    if (text.size > 1048576) throw denied();
                    content.text = await text.text();
                }
                if (item?.types.includes('image/png')) {
                    const image = await item.getType('image/png');
                    if (image.size > 16777216) throw denied();
                    content.png = await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result.split(',')[1]);
                        reader.onerror = () => reject(denied());
                        reader.readAsDataURL(image);
                    });
                }
                if (Object.keys(content).length) await forward(content);
            }
        });
    }
    // Libraries using a temporary textarea must be observed before it is removed.
    const exec = Document.prototype.execCommand;
    Object.defineProperty(Document.prototype, 'execCommand', {
        configurable: true, writable: true,
        value: function(command, ...args) {
            const type = String(command).toLowerCase();
            if (this !== document || !['copy', 'cut'].includes(type))
                return exec.call(this, command, ...args);
            let event, text, custom = false;
            const capture = e => {
                if (!e.isTrusted) return;
                event = e;
                const field = document.activeElement;
                if (field?.type === 'password') return;
                text = field && typeof field.selectionStart === 'number'
                    ? field.value.slice(field.selectionStart, field.selectionEnd)
                    : String(getSelection());
            };
            const bubble = e => {
                if (e === event && e.defaultPrevented) {
                    text = e.clipboardData?.types.includes('text/plain')
                        ? e.clipboardData.getData('text/plain') : undefined;
                    custom = true;
                }
            };
            addEventListener(type, capture, true);
            addEventListener(type, bubble);
            let copied;
            try { copied = exec.call(this, command, ...args); }
            finally {
                removeEventListener(type, capture, true);
                removeEventListener(type, bubble);
            }
            if (copied && event && (!event.defaultPrevented || custom) &&
                typeof text === 'string' && text.length <= 1048576)
                forward({kind: 'legacy', text}).catch(() => {});
            return copied;
        }
    });
})();
"""

ISOLATED_SCRIPT = r"""
(() => {
    if (window !== top) return;
    let legacy = false;
    for (const type of ['copy', 'cut']) addEventListener(type, event => {
        legacy = event.isTrusted && navigator.userActivation.isActive && document.hasFocus() &&
                 document.activeElement?.type !== 'password';
        setTimeout(() => { legacy = false; }, 0);
    }, true);
    const imageBlob = png => new Blob([Uint8Array.from(atob(png), c => c.charCodeAt(0))],
                                      {type: 'image/png'});
    // Called only by the host after an explicit native Paste, in this isolated world.
    globalThis.miniBrowserPasteImage = async png => {
        if (!document.hasFocus() || !navigator.clipboard) return false;
        await navigator.clipboard.write([new ClipboardItem({'image/png': imageBlob(png)})]);
        return true;
    };
    globalThis.miniBrowserClipboardReply = (id, error) => {
        dispatchEvent(new CustomEvent('mini-browser-clipboard-response', {
            detail: JSON.stringify({id, error})
        }));
    };
    addEventListener('mini-browser-clipboard-request', event => {
        if (typeof event.detail !== 'string' || event.detail.length > 28662104) return;
        let request;
        try { request = JSON.parse(event.detail); } catch { return; }
        if (!request || !Number.isSafeInteger(request.id) || request.id <= 0) return;
        if (!navigator.userActivation.isActive || !document.hasFocus()) {
            miniBrowserClipboardReply(request.id, true);
            return;
        }
        if (request.kind === 'legacy') {
            if (legacy && typeof request.text === 'string' && request.png === undefined) {
                legacy = false;
                miniBrowserWriteClipboard(event.detail);
            } else miniBrowserClipboardReply(request.id, true);
            return;
        }
        if (!isSecureContext || !navigator.clipboard ||
            (request.kind !== undefined && request.kind !== 'write')) {
            miniBrowserClipboardReply(request.id, true);
            return;
        }
        let written;
        try {
            if (typeof request.png === 'string' && request.png.length <= 22369624) {
                const content = {'image/png': imageBlob(request.png)};
                if (typeof request.text === 'string')
                    content['text/plain'] = new Blob([request.text], {type: 'text/plain'});
                written = navigator.clipboard.write([new ClipboardItem(content)]);
            } else if (typeof request.text === 'string' && request.png === undefined) {
                written = navigator.clipboard.writeText(request.text);
            } else throw new Error();
        } catch (_) { miniBrowserClipboardReply(request.id, true); return; }
        written.then(
            () => miniBrowserWriteClipboard(event.detail),
            () => miniBrowserClipboardReply(request.id, true)
        );
    });
})();
"""


def clipboard_origin(uri, *, extension_origin="", legacy=False):
    """Normalize clipboard origins; legacy copy also supports ordinary HTTP."""
    try:
        parts = urlsplit(uri)
        host = (parts.hostname or "").rstrip(".")
        if (
            extension_origin
            and parts.scheme == "chrome-extension"
            and len(host) == 32
            and all("a" <= char <= "p" for char in host)
            and parts.netloc == host
            and f"chrome-extension://{host}" == extension_origin
            and parts.username is None
            and parts.password is None
            and parts.port is None
        ):
            return parts.scheme, host, None
        local = host == "localhost" or host.endswith(".localhost")
        try:
            local |= ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if (
            host
            and parts.username is None
            and parts.password is None
            and (parts.scheme == "https" or parts.scheme == "http" and (local or legacy))
        ):
            return parts.scheme, host, parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        pass
    return None


def clipboard_content(request):
    """Validate bounded text/PNG data before touching the desktop clipboard."""
    text, png = request.get("text"), request.get("png")
    if "text" in request and (
        not isinstance(text, str) or "\0" in text or len(text.encode("utf-8")) > LIMIT
    ):
        raise ValueError("Invalid clipboard text")
    if "png" in request:
        if not isinstance(png, str) or len(png) > (IMAGE_LIMIT + 2) // 3 * 4:
            raise ValueError("Clipboard image is too large")
        png = base64.b64decode(png, validate=True)
        if len(png) < 24 or len(png) > IMAGE_LIMIT or png[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR":
            raise ValueError("Invalid clipboard PNG")
        width, height = struct.unpack(">II", png[16:24])
        if not width or not height or width * height > PIXEL_LIMIT:
            raise ValueError("Clipboard image dimensions are too large")
    if text is None and png is None:
        raise ValueError("Clipboard contains no supported content")
    return text, png


def write_clipboard(clipboard, text, png):
    if png is None:
        clipboard.set(text)
        return
    from gi.repository import Gdk, GLib, GObject

    try:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
    except GLib.Error as error:
        raise ValueError("Invalid clipboard PNG") from error
    # The concrete MemoryTexture type has no clipboard serializer. Advertise
    # its Texture base type so other processes receive image/png as well.
    providers = [Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture))]
    if text is not None:
        providers.append(Gdk.ContentProvider.new_for_value(text))
    if not clipboard.set_content(Gdk.ContentProvider.new_union(providers)):
        raise ValueError("Could not set the desktop clipboard")


class PageClipboard:
    def __init__(self, view):
        self.view = view
        self.last_input = 0
        self.paste_serial = 0

    def reset(self):
        self.last_input = 0
        self.paste_serial += 1

    def user_input(self):
        self.last_input = time.monotonic()

    def _active(self, generation):
        view = self.view
        root = view.get_root()
        return (
            not view.closed
            and generation == view.generation
            and view.get_mapped()
            and view.has_focus()
            and root is not None
            and root.is_active()
        )

    def paste(self, *, plain=False):
        """Read the desktop only for a native keyboard or context-menu Paste."""
        from gi.repository import Gdk, GLib

        view = self.view
        generation = view.generation
        self.paste_serial += 1
        serial = self.paste_serial
        clipboard = view.get_clipboard()

        def current():
            return serial == self.paste_serial and self._active(generation)

        def text_ready(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text and current():
                    view.command("Input.insertText", {"text": text})
            except GLib.Error:
                pass

        def image_ready(clipboard, result):
            try:
                texture = clipboard.read_texture_finish(result)
                if not current():
                    return
                if texture is None:
                    clipboard.read_text_async(None, text_ready)
                    return
                if texture.get_width() * texture.get_height() > PIXEL_LIMIT:
                    return
                png = texture.save_to_png_bytes().get_data()
                if len(png) <= IMAGE_LIMIT:
                    self._paste_image(base64.b64encode(png).decode("ascii"), current)
            except GLib.Error:
                if current():
                    clipboard.read_text_async(None, text_ready)

        formats = clipboard.get_formats()
        if not plain and (
            formats.contain_gtype(Gdk.Texture)
            or any(mime.startswith("image/") for mime in formats.get_mime_types())
        ):
            clipboard.read_texture_async(None, image_ready)
        else:
            clipboard.read_text_async(None, text_ready)

    def _paste_image(self, png, current):
        view = self.view

        def frames_ready(result, error):
            if error or not current():
                return
            frame = (result or {}).get("frameTree", {}).get("frame", {}).get("id")
            context = next(
                (
                    context
                    for context in view.contexts.values()
                    if context.get("name") == WORLD
                    and context.get("auxData", {}).get("frameId") == frame
                    and context.get("uniqueId")
                ),
                None,
            )
            if not context:
                return
            options = {"extension_origin": getattr(view, "extension_origin", "")}
            origin = clipboard_origin(view.get_uri() or "", **options)
            if origin is None or clipboard_origin(context.get("origin", ""), **options) != origin:
                return
            unique = context["uniqueId"]

            def prepared(result, error):
                if (
                    error
                    or not current()
                    or view.contexts.get(context["id"], {}).get("uniqueId") != unique
                    or not (result or {}).get("result", {}).get("value")
                ):
                    return
                # A real Chromium editing command supplies a trusted paste event
                # with File/clipboardData support, including website upload handlers.
                view.command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "rawKeyDown",
                        "key": "v",
                        "code": "KeyV",
                        "windowsVirtualKeyCode": 86,
                        "modifiers": 2,
                        "commands": ["Paste"],
                    },
                )
                view.command(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "key": "v",
                        "code": "KeyV",
                        "windowsVirtualKeyCode": 86,
                        "modifiers": 2,
                    },
                )

            view.command(
                "Runtime.callFunctionOn",
                {
                    "uniqueContextId": unique,
                    "functionDeclaration": "function(png){return miniBrowserPasteImage(png)}",
                    "arguments": [{"value": png}],
                    "awaitPromise": True,
                    "returnByValue": True,
                    "userGesture": True,
                },
                prepared,
            )

        view.command("Page.getFrameTree", callback=frames_ready)

    def copy_image(self, source, frame_url, completed):
        """Copy the loaded image resource, including cross-origin images, as PNG."""
        from gi.repository import Gdk, GLib

        view, generation = self.view, self.view.generation

        def current():
            return not view.closed and view.generation == generation

        def resource_ready(result, error):
            if not current():
                return
            try:
                content = (result or {}).get("content", "")
                if error or not content or len(content) > IMAGE_LIMIT * 2:
                    raise ValueError("Image resource is unavailable")
                image = (
                    base64.b64decode(content, validate=True)
                    if result.get("base64Encoded")
                    else content.encode("utf-8")
                )
                if len(image) > IMAGE_LIMIT:
                    raise ValueError("Image resource is too large")
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(image))
                if texture.get_width() * texture.get_height() > PIXEL_LIMIT:
                    raise ValueError("Image dimensions are too large")
                png = texture.save_to_png_bytes().get_data()
                if len(png) > IMAGE_LIMIT:
                    raise ValueError("Image is too large")
                write_clipboard(view.get_clipboard(), None, png)
            except (ValueError, TypeError, GLib.Error):
                completed(False)
                return
            completed(True)

        def frames_ready(result, error):
            if not current():
                return
            frames = [(result or {}).get("frameTree", {})]
            while frames and not error:
                tree = frames.pop()
                frame = tree.get("frame", {})
                if frame.get("url") == frame_url:
                    view.command(
                        "Page.getResourceContent",
                        {"frameId": frame["id"], "url": source},
                        resource_ready,
                    )
                    return
                frames.extend(tree.get("childFrames", []))
            completed(False)

        view.command("Page.getFrameTree", callback=frames_ready)

    def handle(self, data):
        if data.get("name") != BINDING:
            return
        view = self.view
        context = view.contexts.get(data.get("executionContextId"), {})
        unique = context.get("uniqueId")
        if context.get("name") != WORLD or not unique:
            return
        payload = data.get("payload", "")
        if not isinstance(payload, str) or len(payload) > PAYLOAD_LIMIT:
            return
        try:
            request = json.loads(payload)
        except (ValueError, RecursionError):
            return
        if (
            not isinstance(request, dict)
            or type(request.get("id")) is not int
            or not 0 < request["id"] < 2**53
        ):
            return
        generation = view.generation

        def checked(result, error):
            if (
                view.closed
                or view.generation != generation
                or view.contexts.get(context["id"], {}).get("uniqueId") != unique
            ):
                return
            frame = (result or {}).get("frameTree", {}).get("frame", {})
            root = view.get_root()
            extension_origin = getattr(view, "extension_origin", "")
            kind = request.get("kind", "write")
            options = {"extension_origin": extension_origin, "legacy": kind == "legacy"}
            origin = clipboard_origin(view.get_uri() or "", **options)
            allowed = (
                not error
                and kind in ("write", "legacy")
                and (kind != "legacy" or "png" not in request)
                and context.get("auxData", {}).get("frameId") == frame.get("id")
                and origin is not None
                and clipboard_origin(context.get("origin", ""), **options) == origin
                and view.get_mapped()
                and view.has_focus()
                and root is not None
                and root.is_active()
                and self.last_input > 0
                and time.monotonic() - self.last_input <= 5
            )
            if allowed:
                try:
                    text, png = clipboard_content(request)
                    write_clipboard(view.get_clipboard(), text, png)
                except (ValueError, TypeError):
                    allowed = False
            else:
                allowed = False
            view.command(
                "Runtime.callFunctionOn",
                {
                    "uniqueContextId": unique,
                    "functionDeclaration": (
                        "function(id,error){globalThis.miniBrowserClipboardReply(id,error)}"
                    ),
                    "arguments": [{"value": request["id"]}, {"value": not allowed}],
                },
            )

        view.command("Page.getFrameTree", callback=checked)
