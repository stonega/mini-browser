# Agent control and UI testing

Home's Apps tab ends with a selectable, monospace **Agent prompt** and **Copy prompt** button.
Paste it into an agent chat for CLI discovery, page/native inspection, saving
Home apps and screenshot verification. Copying it does not enable agent control.

Start Mini Browser with local control enabled:

```sh
mini-browser --agent
mini-browser agent list
mini-browser agent open https://example.com
```

From a source checkout, substitute `./scripts/run` for `mini-browser` in these
commands. Normal launches leave control disabled. `--agent` also enables control
on an already running instance. It keeps the application running after its last
window closes; `mini-browser agent stop` or the application's Quit action stops it.
All browser windows in that application are accessible to the agent.

Commands print one JSON result to stdout. Errors go to stderr as JSON and return
status 1; argument syntax errors return status 2. Page IDs come from `list` or
`open`; never assume a particular number. A page ID remains stable until its
window closes. Navigation and resizing return when requested, before completion.
Poll `list` for the desired URL/title, `loading: false` and `ready: true`, then take
a fresh snapshot. Errors and failed loads may also finish loading, so check the
reported layout and expected title/content as well.

## Command reference

`PAGE` below is an integer page ID. Run `mini-browser agent --help`, or add
`--help` to any subcommand, for argument syntax.

| Command after `mini-browser agent` | Purpose |
| --- | --- |
| `list` | List browser windows, page IDs, title, URL, layout and loading state |
| `open [URL]` | Open a native window at a URL/search, or Home |
| `add-app URL [--title NAME]` | Save a persistent app tile on Home without opening the URL |
| `apps` | List saved Home apps |
| `remove-app URL` | Remove a saved Home app |
| `navigate PAGE URL` | Navigate an existing window |
| `close PAGE` | Close a window and destroy its Chromium view |
| `snapshot PAGE` | Read the Chromium accessibility tree and current element uids |
| `click PAGE --uid UID` | Click an element from the latest snapshot |
| `click PAGE --selector CSS` | Click a visible main-document CSS match |
| `fill PAGE --uid UID VALUE` | Fill an input, textarea or select and emit input/change |
| `fill PAGE --selector CSS VALUE` | Fill a main-document CSS match |
| `press PAGE KEY` | Page keys such as `Enter`, `Tab`, `Escape`, `ArrowDown`, `Control+A` |
| `eval PAGE '() => …'` | Run an explicit page JavaScript function; return its JSON value |
| `screenshot PAGE new.png` | Capture web content as a PNG |
| `screenshot PAGE new.png --native` | Capture the GTK window, header and visible dialogs |
| `resize PAGE WIDTH HEIGHT` | Request native dimensions, 200–4096 logical pixels |
| `inspect PAGE` | Inspect mapped GTK widgets and available native actions |
| `native-click PAGE UID` | Activate a button/menu from the latest native inspection |
| `native-fill PAGE UID VALUE [--submit]` | Fill a native text entry, optionally activating it |
| `action PAGE ACTION` | Activate an enabled native window action |
| `cdp PAGE METHOD '[JSON]'` | Send a raw Chromium DevTools command; JSON defaults to `{}` |
| `tools` | Print every MCP tool and its argument schema, without connecting |
| `call TOOL '[JSON]'` | Call any MCP tool with a JSON object |
| `stop` | Quit the agent-enabled browser |

Use exactly one of `--uid` and `--selector` for element input. Uids are specific
to a page, document and snapshot. Taking another web/native snapshot replaces
that page's earlier uids of the same kind. Navigation invalidates web uids. Removed
or hidden elements may require another snapshot. CSS selectors target the main
document; shadow-root selectors, child-target sessions and frame selection are
not implemented. The web snapshot returns up to 4,000 AX nodes with a truncation
indicator; the native snapshot is bounded at 2,000 widgets. Page key commands
target Chromium. Native shortcuts should use `action` instead. Filling sets the
DOM value and dispatches events; it does not simulate individual keystrokes.

For a hypothetical page 3 with a search form:

```sh
mini-browser agent snapshot 3
mini-browser agent fill 3 --selector 'input[name=q]' 'synthetic search'
mini-browser agent press 3 Enter
mini-browser agent eval 3 '() => ({title: document.title, url: location.href})'
mini-browser agent screenshot 3 /tmp/mini-browser-web.png
mini-browser agent cdp 3 Runtime.evaluate '{"expression":"document.title","returnByValue":true}'
```

Screenshot filenames must be new files; the CLI never overwrites an existing file
or follows a destination symlink. Images are created with mode 0600. The MCP
screenshot tool returns an image directly instead of writing a file.

## Saved Home apps

```sh
mini-browser --agent
mini-browser agent add-app https://example.com/dashboard --title 'My dashboard'
mini-browser agent apps
mini-browser agent remove-app https://example.com/dashboard
```

Saved apps appear in **Apps**, alongside devices and script apps, before presets
and bookmark sites. They survive restarts and remain visible as history changes.
Existing Home windows refresh immediately. Saving does not open the site, fetch its title
or icon, or add a history/bookmark record. Clicking the tile opens its saved URL.

Use a full `http://` or `https://` URL, including loopback URLs for local apps.
URLs containing usernames/passwords are rejected. Names default to the hostname;
`--title` accepts 1–200 characters of plain text. Repeating a URL updates the
existing tile; omitting `--title` preserves its saved name and position. Hostname
case, default ports and a missing root slash normalize to the same URL. Paths,
query strings and fragments remain distinct, so one site can have several apps.
Bookmark-derived tiles for a saved app's origin are hidden while it is saved.
Removing a saved tile leaves bookmarks and browsing history intact; the website
may still appear as a bookmarked app or in History. DeepSeek keeps its built-in
tile in Apps. The menu's **Bookmark App** action saves a website root;
agent-saved Home apps continue to open their explicit URLs.

The same operations are available as MCP tools `add_home_app`, `list_home_apps`
and `remove_home_app`, or through `agent call`. They do not require a page ID.

## Codex native MCP setup

Register the built-in stdio server with Codex:

```sh
codex mcp add mini_browser -- mini-browser mcp
```

Or add this to `~/.codex/config.toml` or a trusted project's `.codex/config.toml`:

```toml
[mcp_servers.mini_browser]
command = "mini-browser"
args = ["mcp"]
tool_timeout_sec = 30
```

For development, use an absolute path to this checkout's `scripts/run` as
`command`, with the same `args`. Start the GUI separately with `--agent` in the
same user session. MCP startup and tool discovery work even before the GUI starts;
browser calls explain how to enable control when it is unavailable. The server
uses newline-delimited JSON-RPC over stdio, with initialization, ping,
`tools/list` and `tools/call`. It negotiates MCP 2025-11-25, 2025-06-18,
2025-03-26 or 2024-11-05. It requires no Node.js runtime or browser extension.

The tools are `list_home_apps`, `add_home_app`, `remove_home_app`,
`list_pages`, `new_page`, `navigate_page`, `close_page`,
`take_snapshot`, `click`, `fill`, `press_key`, `evaluate_script`, `take_screenshot`,
`resize_page`, `native_snapshot`, `native_click`, `native_fill`, `activate_action`, `cdp_send`
and `quit_browser`. Page-scoped tools require `pageId`. For example:

```json
{"name":"fill","arguments":{"pageId":3,"selector":"input[name=q]","value":"synthetic search"}}
```

This is Mini Browser's native MCP server with DevTools-backed commands. It is
not a complete implementation of Google's `chrome-devtools-mcp` tool catalog,
and does not register Mini Browser in the Codex Chrome extension's browser picker
or implement its private native-messaging transport. The inspected installed
ChatGPT extension (1.26.1020.11845) sends CDP through `chrome.debugger.sendCommand`;
Mini Browser sends commands directly through its existing in-process CEF bridge.
`cdp_send` returns a command's result; event streaming and attached child-target
sessions are not exposed. There is no HTTP/WebSocket remote debugging endpoint.

See the official [Codex MCP configuration guide](https://developers.openai.com/codex/mcp/),
[MCP stdio transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
and [Chromium DevTools protocol](https://chromedevtools.github.io/devtools-protocol/).

## Native UI testing

Native actions include `home`, `back`, `forward`, `reload`,
`reload-bypass-cache`, `stop`, `address`, `settings`, `about`, `shortcuts`,
`show-header`, `page-layout`, `audio-player`, `video-player`, `zoom-in`,
`zoom-out`, and `zoom-reset`. `inspect` reports which are currently enabled,
plus mapped widget types, parents, labels, icons, tooltips, placeholders and CSS classes.
Use observed widget uids for `native-click` and `native-fill`; native editable values are excluded.
Password dialogs block native inspection, button activation and screenshots.
Vault unlock, import and credential filling are not agent tools.

```sh
mini-browser agent inspect 3
mini-browser agent action 3 home
mini-browser agent inspect 3
# Use the GtkEntry uid returned by inspect, replacing GTK_UID:
mini-browser agent native-fill 3 GTK_UID http://127.0.0.1:8000 --submit
mini-browser agent action 3 settings
mini-browser agent inspect 3
mini-browser agent screenshot 3 /tmp/mini-browser-settings.png --native
mini-browser agent resize 3 600 700
```

For development, run the integrated test on a disposable GNOME desktop:

```sh
./scripts/check
./scripts/smoke --test=agent
./scripts/smoke
```

The agent smoke test uses generated loopback pages, a temporary browser profile,
the public CLI and a real MCP subprocess. It covers snapshots, trusted mouse
input, Unicode form filling, Enter, script/CDP results, web/native screenshots,
Settings, native button activation, Home/address entry submission, stale uids, multiple windows,
saved-app updates and tile activation, last-window lifetime and shutdown cleanup.
It never uses the active desktop or
real Chrome profiles.

## Local transport and limits

The default socket is `$XDG_RUNTIME_DIR/mini-browser-agent/control.sock`
(falling back to `/run/user/UID/mini-browser-agent/control.sock`). Its directory
must be owned by the current user with mode 0700; the socket uses mode 0600.
Linux peer credentials restrict connections to the same account. Live sockets,
regular files and symlinks are not replaced; a refused stale socket is removed
on the next explicit startup. Shutdown removes the socket.

To select a different private path, set `MINI_BROWSER_AGENT_SOCKET` for the GUI
and client, or use `mini-browser agent --socket /private/path/control.sock …`
and `mini-browser mcp --socket /private/path/control.sock`. The containing
directory must be private. Only one agent-enabled application can own a socket.

Control grants the local agent development access to loaded websites and their
session, including arbitrary page JavaScript and raw CDP. Enable it for agents
you trust. It exposes no vault API, saves no command history and logs no page
contents or entered values. The Chromium sandbox and TLS verification stay enabled.
Messages are limited to 12 MiB; the existing CEF result cap is 8 MiB. At most
eight socket requests are accepted concurrently, and requests time out after
20 seconds. After a timeout/disconnect, inspect current state before retrying a
mutation: a command already submitted to Chromium may have completed.
