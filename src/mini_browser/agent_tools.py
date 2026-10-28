"""Shared public command schemas for the CLI and native stdio MCP server."""

STRING = {"type": "string"}
PAGE = {"pageId": {"type": "integer", "minimum": 1}}
ELEMENT = {**PAGE, "uid": STRING, "selector": STRING}


def tool(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "annotations": {
            "readOnlyHint": name
            in {
                "list_pages",
                "list_home_apps",
                "take_snapshot",
                "take_screenshot",
                "native_snapshot",
            },
        },
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOLS = [
    tool("list_home_apps", "List saved Home app tiles, in their display order."),
    tool(
        "add_home_app",
        "Save a persistent app tile on Home without opening its URL. Repeated URLs update "
        "the existing tile; omitting title preserves its name. Requires a full HTTP(S) URL.",
        {"url": STRING, "title": STRING},
        ("url",),
    ),
    tool(
        "remove_home_app",
        "Remove a saved Home app tile by URL, leaving browsing history unchanged.",
        {"url": STRING},
        ("url",),
    ),
    tool("list_pages", "List Mini Browser page IDs, URLs, titles and load state."),
    tool("new_page", "Open a native browser window; navigation is asynchronous.", {"url": STRING}),
    tool(
        "navigate_page",
        "Navigate a page; poll list_pages or snapshot for readiness.",
        {**PAGE, "url": STRING},
        ("pageId", "url"),
    ),
    tool("close_page", "Close one page window.", PAGE, ("pageId",)),
    tool(
        "take_snapshot",
        "Read the Chromium accessibility tree. Use its latest uids for input.",
        PAGE,
        ("pageId",),
    ),
    tool(
        "click",
        "Click a visible element using exactly one latest uid or CSS selector.",
        ELEMENT,
        ("pageId",),
    ),
    tool(
        "fill",
        "Set an input, textarea or select value and dispatch input/change events. "
        "Provide exactly one uid or CSS selector.",
        {**ELEMENT, "value": STRING},
        ("pageId", "value"),
    ),
    tool(
        "press_key",
        "Send a page key, such as Enter, Tab, Escape or Control+A. "
        "For native shortcuts use activate_action.",
        {**PAGE, "key": STRING},
        ("pageId", "key"),
    ),
    tool(
        "evaluate_script",
        "Run an explicit JavaScript function in the main page and return JSON. "
        "Promises are awaited; no access to the app's encrypted vault.",
        {**PAGE, "function": STRING},
        ("pageId", "function"),
    ),
    tool(
        "take_screenshot",
        "Capture PNG web content, or native window including its header/dialogs.",
        {**PAGE, "native": {"type": "boolean"}},
        ("pageId",),
    ),
    tool(
        "resize_page",
        "Request native window dimensions in logical pixels (200–4096).",
        {
            **PAGE,
            "width": {"type": "integer", "minimum": 200, "maximum": 4096},
            "height": {"type": "integer", "minimum": 200, "maximum": 4096},
        },
        ("pageId", "width", "height"),
    ),
    tool(
        "native_snapshot",
        "Inspect visible GTK widgets and available native actions. "
        "Editable contents and password dialogs are excluded.",
        PAGE,
        ("pageId",),
    ),
    tool(
        "native_click",
        "Activate a GTK button/menu using its latest native_snapshot uid.",
        {**PAGE, "uid": STRING},
        ("pageId", "uid"),
    ),
    tool(
        "native_fill",
        "Fill an observed GTK entry, such as Home or address. Optionally submit it. "
        "Password fields and dialogs are excluded.",
        {**PAGE, "uid": STRING, "value": STRING, "submit": {"type": "boolean"}},
        ("pageId", "uid", "value"),
    ),
    tool(
        "activate_action",
        "Activate an enabled native action, e.g. home, settings, back, "
        "reload, page-layout, audio-player, video-player. See native_snapshot for all actions.",
        {**PAGE, "action": STRING},
        ("pageId", "action"),
    ),
    tool(
        "cdp_send",
        "Send a raw in-process Chromium DevTools command to a page. "
        "Advanced development access; commands can change browsing state.",
        {**PAGE, "method": STRING, "params": {"type": "object"}},
        ("pageId", "method"),
    ),
    tool("quit_browser", "Quit the agent-enabled browser and stop its local control server."),
]


def validate(name, arguments):
    from .control import ControlError

    definition = next((item for item in TOOLS if item["name"] == name), None)
    if definition is None:
        raise ControlError("Unknown tool. Use tools/list or agent --help.")
    if not isinstance(arguments, dict):
        raise ControlError("Tool arguments must be an object.")
    schema = definition["inputSchema"]
    if any(key not in schema["properties"] for key in arguments):
        raise ControlError("Unknown tool argument.")
    if any(key not in arguments for key in schema["required"]):
        raise ControlError("Missing required tool argument.")
    types = {"string": str, "integer": int, "boolean": bool, "object": dict}
    for key, value in arguments.items():
        rule = schema["properties"][key]
        if type(value) is not types[rule["type"]]:
            raise ControlError(f"Invalid type for {key}.")
        if rule["type"] == "integer" and not rule.get("minimum", value) <= value <= rule.get(
            "maximum", value
        ):
            raise ControlError(f"{key} is outside the supported range.")
    return definition
