"""Bridge successful page text copies from windowless Chromium to GTK."""

import ipaddress
import json
import time
from urllib.parse import urlsplit

WORLD = "mini-browser-clipboard"
BINDING = "miniBrowserWriteClipboard"
LIMIT = 1024 * 1024

# Chromium performs its own Clipboard API checks first. The isolated world
# verifies user activation before forwarding a write; no clipboard reads cross
# this bridge. Request/response events carry strings, never executable source.
MAIN_SCRIPT = r"""
(() => {
    if (window !== top || !isSecureContext || !navigator.clipboard) return;
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
    Object.defineProperty(Clipboard.prototype, 'writeText', {
        configurable: true, writable: true,
        value: async function(text) {
            if (this !== navigator.clipboard) throw new TypeError('Illegal invocation');
            text = String(text);
            if (text.length > 1048576 || pending.size >= 8) throw denied();
            return new Promise((resolve, reject) => {
                const id = ++next;
                const timer = setTimeout(() => {
                    pending.delete(id); reject(denied());
                }, 5000);
                pending.set(id, {resolve, reject, timer});
                dispatchEvent(new CustomEvent('mini-browser-clipboard-request', {
                    detail: JSON.stringify({id, text})
                }));
            });
        }
    });
})();
"""

ISOLATED_SCRIPT = r"""
(() => {
    if (window !== top || !isSecureContext) return;
    globalThis.miniBrowserClipboardReply = (id, error) => {
        dispatchEvent(new CustomEvent('mini-browser-clipboard-response', {
            detail: JSON.stringify({id, error})
        }));
    };
    addEventListener('mini-browser-clipboard-request', event => {
        if (typeof event.detail !== 'string' || event.detail.length > 2097152) return;
        let request;
        try { request = JSON.parse(event.detail); } catch { return; }
        if (!request || !Number.isSafeInteger(request.id) || request.id <= 0 ||
            typeof request.text !== 'string') return;
        if (!navigator.userActivation.isActive || !document.hasFocus()) {
            miniBrowserClipboardReply(request.id, true);
            return;
        }
        navigator.clipboard.writeText(request.text).then(
            () => miniBrowserWriteClipboard(event.detail),
            () => miniBrowserClipboardReply(request.id, true)
        );
    });
})();
"""


def secure_origin(uri, *, extension_origin=""):
    """Normalize secure web origins or the view's explicitly trusted extension."""
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
            and (parts.scheme == "https" or parts.scheme == "http" and local)
        ):
            return parts.scheme, host, parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        pass
    return None


class PageClipboard:
    def __init__(self, view):
        self.view = view
        self.last_input = 0

    def reset(self):
        self.last_input = 0

    def user_input(self):
        self.last_input = time.monotonic()

    def handle(self, data):
        if data.get("name") != BINDING:
            return
        view = self.view
        context = view.contexts.get(data.get("executionContextId"), {})
        unique = context.get("uniqueId")
        if context.get("name") != WORLD or not unique:
            return
        payload = data.get("payload", "")
        if not isinstance(payload, str) or len(payload) > LIMIT * 2:
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
            origin = secure_origin(view.get_uri() or "", extension_origin=extension_origin)
            allowed = (
                not error
                and context.get("auxData", {}).get("frameId") == frame.get("id")
                and origin is not None
                and secure_origin(context.get("origin", ""), extension_origin=extension_origin)
                == origin
                and view.get_mapped()
                and view.has_focus()
                and root is not None
                and root.is_active()
                and self.last_input > 0
                and time.monotonic() - self.last_input <= 5
            )
            text = request.get("text")
            if allowed and isinstance(text, str) and "\0" not in text:
                try:
                    allowed = len(text.encode("utf-8")) <= LIMIT
                    if allowed:
                        view.get_clipboard().set(text)
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
