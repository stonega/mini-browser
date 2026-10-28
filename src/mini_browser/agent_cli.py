"""Agent CLI: machine-readable output, without importing GTK or starting the app."""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from .agent_tools import TOOLS, validate
from .control import ControlError, request


def save_png(filename, data):
    """Create a private image without overwriting an existing file or following a symlink."""
    try:
        decoded = base64.b64decode(data, validate=True)
    except (ValueError, TypeError) as error:
        raise ControlError("Invalid screenshot data.") from error
    if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ControlError("Screenshot is not a PNG image.")
    target = Path(filename)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(decoded)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return str(target.resolve())


def parser():
    result = argparse.ArgumentParser(
        prog="mini-browser agent",
        description="Control a running Mini Browser started with --agent.",
        epilog="All commands return JSON. Use call TOOL JSON for the full MCP tool surface.",
    )
    result.add_argument("--socket", help="Override the private control socket path")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List pages and stable page IDs")
    commands.add_parser("apps", help="List saved Home apps")
    added = commands.add_parser(
        "add-app", help="Save a persistent Home tile without opening a page"
    )
    added.add_argument("url", help="Full http:// or https:// URL")
    added.add_argument(
        "--title", help="App name; defaults to its hostname, or preserves an existing name"
    )
    removed = commands.add_parser("remove-app", help="Remove a saved Home tile by URL")
    removed.add_argument("url")
    commands.add_parser("stop", help="Quit the agent-enabled browser")
    commands.add_parser("tools", help="List all tools and JSON argument schemas")
    opened = commands.add_parser("open", help="Open a window, optionally at a URL or search")
    opened.add_argument("url", nargs="?")
    for name in (
        "navigate",
        "close",
        "snapshot",
        "inspect",
        "click",
        "fill",
        "press",
        "eval",
        "screenshot",
        "resize",
        "action",
        "native-click",
        "native-fill",
        "cdp",
    ):
        command = commands.add_parser(name)
        command.add_argument("pageId", type=int)
        if name == "navigate":
            command.add_argument("url")
        elif name in ("click", "fill"):
            element = command.add_mutually_exclusive_group(required=True)
            element.add_argument("--uid")
            element.add_argument("--selector")
            if name == "fill":
                command.add_argument("value")
        elif name in ("press", "eval", "action", "native-click"):
            command.add_argument(
                {"press": "key", "eval": "function", "action": "action", "native-click": "uid"}[
                    name
                ]
            )
        elif name == "native-fill":
            command.add_argument("uid")
            command.add_argument("value")
            command.add_argument("--submit", action="store_true")
        elif name == "screenshot":
            command.add_argument("file", help="New PNG file; existing files are never overwritten")
            command.add_argument(
                "--native", action="store_true", help="Include GTK header and dialogs"
            )
        elif name == "resize":
            command.add_argument("width", type=int)
            command.add_argument("height", type=int)
        elif name == "cdp":
            command.add_argument("method")
            command.add_argument("params", nargs="?", default="{}", help="JSON protocol parameters")
    call = commands.add_parser("call", help="Call any tool with a JSON object of arguments")
    call.add_argument("tool", choices=[item["name"] for item in TOOLS])
    call.add_argument("arguments", nargs="?", default="{}")
    return result


def main(argv=None):
    options = vars(parser().parse_args(argv))
    command, path = options.pop("command"), options.pop("socket")
    try:
        if command == "tools":
            print(json.dumps({"tools": TOOLS}))
            return 0
        aliases = {
            "apps": "list_home_apps",
            "add-app": "add_home_app",
            "remove-app": "remove_home_app",
            "list": "list_pages",
            "open": "new_page",
            "navigate": "navigate_page",
            "close": "close_page",
            "snapshot": "take_snapshot",
            "inspect": "native_snapshot",
            "press": "press_key",
            "eval": "evaluate_script",
            "screenshot": "take_screenshot",
            "resize": "resize_page",
            "action": "activate_action",
            "native-click": "native_click",
            "native-fill": "native_fill",
            "cdp": "cdp_send",
            "stop": "quit_browser",
        }
        filename = options.pop("file", None)
        if command == "call":
            method = options["tool"]
            options = json.loads(options["arguments"])
        else:
            method = aliases.get(command, command)
            options = {key: value for key, value in options.items() if value is not None}
            if command == "cdp":
                options["params"] = json.loads(options["params"])
        validate(method, options)
        result = request(method, options, path=path)
        if filename:
            result = {"file": save_png(filename, result["data"])}
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except ControlError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except FileExistsError:
        print(
            json.dumps({"error": "Screenshot file already exists. Choose a new PNG path."}),
            file=sys.stderr,
        )
        return 1
    except (ValueError, OSError, RecursionError):
        # Do not echo arguments, scripts or entered values on failure.
        print(
            json.dumps(
                {
                    "error": "Agent command failed. Check arguments, page readiness and "
                    "that Mini Browser is running with --agent."
                }
            ),
            file=sys.stderr,
        )
        return 1
