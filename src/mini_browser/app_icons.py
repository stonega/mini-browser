"""App icon identities shared by Home and the headless panel catalog."""

import unicodedata
from pathlib import Path

DEEPSEEK_ICON = Path(__file__).with_name("icons") / "deepseek.png"


def website_avatar(site):
    text = site.split("://", 1)[1].removeprefix("www.")
    # Match Adw.Avatar's g_str_hash(text) % 14 palette selection, including
    # GLib's signed UTF-8 bytes and unsigned 32-bit overflow.
    color_hash = 5381
    for byte in text.encode("utf-8"):
        color_hash = (color_hash * 33 + (byte if byte < 128 else byte - 256)) & 0xFFFFFFFF
    return {
        "text": text,
        "initials": unicodedata.normalize("NFC", text.upper())[0],
        "color": color_hash % 14 + 1,
    }
