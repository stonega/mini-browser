import sys

from . import VERSION

if "--version" in sys.argv:
    print(f"Mini Browser {VERSION}")
    raise SystemExit(0)
if "--help" in sys.argv or "-h" in sys.argv:
    print(
        "Usage: mini-browser [URL or 'search terms']…\n"
        "Each argument opens a separate portrait window.\n"
        "Run without arguments for the start page.\n"
        "Ctrl+L: address  Ctrl+N: new window  Ctrl+Shift+I: import  F1: shortcuts"
    )
    raise SystemExit(0)

try:
    from .application import main
    from .chromium import Bridge

    Bridge()  # Fail before opening a window if the native runtime is missing.
    result = main()
except (ImportError, ValueError, RuntimeError) as exc:
    print(
        "Mini Browser needs GTK 4, libadwaita, the bundled Chromium runtime and cryptography 44+: "
        + str(exc),
        file=sys.stderr,
    )
    raise SystemExit(1) from None

raise SystemExit(result)
