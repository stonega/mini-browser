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
except (ImportError, ValueError) as exc:
    print(f"Mini Browser needs GTK 4, libadwaita and WebKitGTK 6.0: {exc}", file=sys.stderr)
    raise SystemExit(1) from None

raise SystemExit(main())
