"""The pinned, offline Android server distributed with Mini Browser."""

import hashlib
from pathlib import Path

VERSION = "5.0.1"
SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"
BUNDLE = Path(__file__).with_name("vendor") / "scrcpy"


def server_path():
    """Reject missing, truncated, or modified helpers before sending them to a phone."""
    path = BUNDLE / "scrcpy-server"
    with path.open("rb") as source:
        content = source.read(2 * 1024 * 1024 + 1)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("Bundled scrcpy server checksum mismatch")
    return path
