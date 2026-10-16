"""Address handling shared by the launcher and native address dialog."""

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit


def is_web_url(value: str) -> bool:
    """Only HTTP(S) URLs may enter the library or be opened from imports."""
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.port != 0
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        return False


def address_to_uri(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter a website address or a search.")
    if re.match(r"^https?://", value, re.IGNORECASE):
        value = value[: value.index(":")].lower() + value[value.index(":") :]
        if not is_web_url(value):
            raise ValueError("This website address is not valid.")
        return value

    # A host with a port is not a URI scheme (example.org:8080 or localhost:3000).
    try:
        candidate = urlsplit("//" + value)
    except ValueError:
        raise ValueError("This website address is not valid.") from None
    # Reject explicit non-web schemes before interpreting domain names or userinfo.
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(.*)", value)
    if scheme and not re.match(r"^\d+(?:[/#?]|$)", scheme[2]):
        raise ValueError("Mini Browser opens http:// and https:// websites.")
    host = candidate.hostname or ""
    local = host.lower() == "localhost" or host.lower().endswith(".localhost")
    try:
        ipaddress.ip_address(host)
        local = True
    except ValueError:
        pass
    if not any(character.isspace() for character in value) and (local or "." in host):
        uri = ("http://" if local else "https://") + value
        if is_web_url(uri):
            return uri
        raise ValueError("This website address is not valid.")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        raise ValueError("Mini Browser opens http:// and https:// websites.")
    return "https://duckduckgo.com/?q=" + quote_plus(value)
