import sys

from . import VERSION

# Route control commands before GTK imports and before the GUI's help handling.
if sys.argv[1:2] in (["agent"], ["mcp"]):
    if sys.argv[1] == "agent":
        from .agent_cli import main as control_main
    else:
        from .mcp import main as control_main
    raise SystemExit(control_main(sys.argv[2:]))

if "--version" in sys.argv:
    print(f"Mini Browser {VERSION}")
    raise SystemExit(0)
if "--help" in sys.argv or "-h" in sys.argv:
    print(
        "Usage: mini-browser [--agent] [URL or 'search terms']…\n"
        "Each argument opens a separate portrait window.\n"
        "Run without arguments for the start page.\n"
        "--agent: enable private local agent control (keeps the app running).\n"
        "mini-browser agent --help: JSON control commands and UI testing\n"
        "mini-browser mcp: native MCP stdio server for Codex\n"
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
