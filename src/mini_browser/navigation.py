"""Address handling shared by the launcher and native address dialog."""

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit

# Keep native link/form navigation (including POST bodies) in this WebView.
# Policy/create handlers also cover script-created popups and modified clicks.
RETARGET = """
const retarget = (element, attribute, fallback) => {
    const target = element.getAttribute(attribute) ?? fallback ?? '';
    if (!target || ['_self', '_parent', '_top'].includes(target.toLowerCase())) return;
    if ([...document.querySelectorAll('iframe, frame')].some(frame => frame.name === target))
        return;
    element.setAttribute(attribute, '_top');
};
window.addEventListener('click', event => {
    const link = event.composedPath().find(node =>
        node instanceof HTMLAnchorElement || node instanceof HTMLAreaElement);
    if (link) retarget(link, 'target', document.querySelector('base[target]')?.target);
}, true);
window.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const fallback = form.getAttribute('target') ?? document.querySelector('base[target]')?.target;
    if (event.submitter?.hasAttribute('formtarget'))
        retarget(event.submitter, 'formtarget', fallback);
    else retarget(form, 'target', fallback);
}, true);
"""


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
