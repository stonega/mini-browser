"""Site permission names, origins and choices. This module has no GTK dependency."""

import ipaddress
import re
from urllib.parse import urlsplit

ASK = "ask"
ALLOW = "allow"
BLOCK = "block"

# Only permissions with a native, useful decision point are listed. Chromium
# can request more types; unsupported requests stay denied.
PERMISSIONS = {
    "camera": ("Camera", "Take photos and record video"),
    "microphone": ("Microphone", "Record audio"),
    "geolocation": ("Location", "Use your approximate location"),
    "notifications": ("Notifications", "Show desktop notifications"),
}
PERMISSION_ORDER = tuple(PERMISSIONS)

# Chromium DevTools permission names, used to persist choices in the shared
# browser context. Clipboard reads stay outside this list; the app's isolated
# clipboard integration never exposes the desktop clipboard to pages.
CDP_NAMES = {
    "camera": "camera",
    "microphone": "microphone",
    "geolocation": "geolocation",
    "notifications": "notifications",
}
CDP_SETTINGS = {ASK: "prompt", ALLOW: "granted", BLOCK: "denied"}

# Values from cef_permission_request_types_t in cef_types.h.
PROMPT_PERMISSION_BITS = {
    1 << 2: "camera",
    1 << 12: "microphone",
    1 << 8: "geolocation",
    1 << 15: "notifications",
}

# Values from cef_media_access_permission_types_t. Desktop capture is not a
# camera or microphone decision, so those bits remain unsupported and denied.
MEDIA_PERMISSION_BITS = {
    1 << 0: "microphone",
    1 << 1: "camera",
}


def normalize_origin(value):
    """Return a credential-free http(s) origin, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("An origin must be text.")
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Enter a valid website origin.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Only http:// and https:// origins can have permissions.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Origins cannot contain a username or password.")
    try:
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise ValueError("The origin has an invalid host or port.") from error
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as error:
            raise ValueError("The origin has an invalid host.") from error
        host = f"[{host.lower()}]"
    else:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ValueError("The origin has an invalid host.") from error
        labels = host.split(".")
        if (
            len(host) > 253
            or any(not label or len(label) > 63 for label in labels)
            or not re.fullmatch(r"[a-z0-9._-]+", host)
        ):
            raise ValueError("The origin has an invalid host.")
    default_port = 443 if parts.scheme == "https" else 80
    if port and port != default_port:
        host += f":{port}"
    return f"{parts.scheme}://{host}"


def origin_host(origin):
    """A short hostname for dialogs; never raises on stored data."""
    try:
        return urlsplit(origin).hostname or origin
    except ValueError:
        return origin


def parse_mask(mask, kind):
    """Split a Chromium permission bitmask into (known names, has_unknown)."""
    table = MEDIA_PERMISSION_BITS if kind == "media-permission" else PROMPT_PERMISSION_BITS
    remaining = mask
    found = set()
    for bit, name in table.items():
        if mask & bit:
            found.add(name)
            remaining &= ~bit
    names = tuple(name for name in PERMISSION_ORDER if name in found)
    return names, bool(remaining)


def describe_permissions(names):
    """Join permission names for a prompt: 'camera and microphone'."""
    labels = [PERMISSIONS[name][0].lower() for name in PERMISSION_ORDER if name in names]
    if not labels:
        return "no permissions"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def decision(library, origin, name):
    """Read a stored choice, treating a missing or invalid row as Ask."""
    state = library.permission_decision(origin, name)
    return state if state in (ALLOW, BLOCK) else ASK


def decide(library, origin, names, state):
    """Persist one choice for each requested permission in an origin."""
    origin = normalize_origin(origin)
    if state not in (ASK, ALLOW, BLOCK):
        raise ValueError("Unknown permission choice.")
    for name in names:
        if name not in PERMISSIONS:
            raise ValueError("Unknown permission.")
        library.set_permission(origin, name, state)
