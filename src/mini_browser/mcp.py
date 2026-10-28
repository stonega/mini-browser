"""Native MCP stdio server. No browser extension, JS runtime or network listener."""

import argparse
import json
import sys

from . import VERSION
from .agent_tools import TOOLS, validate
from .control import LIMIT, ControlError, encode, request

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def rpc(message, *, path=None):
    identifier = message.get("id") if isinstance(message, dict) else None

    def failure(code, text):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": text}}

    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
        or ("id" in message and type(identifier) not in (str, int))
    ):
        return failure(-32600, "Invalid JSON-RPC request")
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return failure(-32602, "params must be an object")
    if method == "initialize":
        offered = params.get("protocolVersion")
        result = {
            "protocolVersion": offered if offered in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mini-browser", "version": VERSION},
            "instructions": "Control Mini Browser started with --agent. Use list_pages, "
            "then take_snapshot for current element uids. Native UI uses native_snapshot "
            "and activate_action. Navigation and resize are asynchronous.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments", {})
        try:
            validate(name, arguments)
        except ControlError as error:
            return failure(-32602, str(error))
        try:
            value = request(name, arguments, path=path)
            if name == "take_screenshot":
                result = {
                    "content": [{"type": "image", "mimeType": "image/png", "data": value["data"]}]
                }
            else:
                result = {
                    "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True)}]
                }
        except (ControlError, ValueError, OSError, RecursionError) as error:
            result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
    else:
        return failure(-32601, "Method not found")
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mini-browser mcp", description="Native MCP server over stdio"
    )
    parser.add_argument("--socket", help="Private socket of an agent-enabled Mini Browser")
    options = parser.parse_args(argv)
    source, output = sys.stdin.buffer, sys.stdout.buffer
    while line := source.readline(LIMIT + 1):
        if len(line) > LIMIT or not line.endswith(b"\n"):
            reply = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Message too large"},
            }
            output.write(encode(reply))
            output.flush()
            return 1
        try:
            message = json.loads(line)
            reply = rpc(message, path=options.socket)
        except (ValueError, RecursionError):
            reply = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if reply is not None:
            output.write(encode(reply))
            output.flush()
    return 0
