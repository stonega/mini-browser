# Architecture

Mini Browser is one native desktop application. Python and PyGObject bind GTK 4,
libadwaita (1.5+), and Chromium through CEF 156. GTK bindings are system packages;
the pinned CEF runtime is bundled. There is no
web UI or JavaScript build toolchain. GTK 4.12+ is required by the CSS API.

`BrowserApplication` owns the application actions, private data paths, SQLite
library, and a shared persistent `chromium.Session`. `BrowserWindow` owns
one WebView and defaults to 420 × 780 logical pixels. Links requesting another
tab/window navigate that same WebView when their origin matches the current page
(scheme, hostname and port); other HTTP(S) destinations open a new native window
and leave the source page intact. Paths, queries and fragments do not affect
this comparison. CEF lifespan/request handlers cancel popup creation and pass
the URL to GTK through a `new-window` event, also covering `window.open()` and
modified clicks. A document-start script in an isolated world keeps submitted
forms in the current WebView, preserving native navigation and POST bodies.
`window.open()` returns no popup handle;
blank popups and programmatic POST popups that bypass submission events are
unsupported. Explicit application new-window actions still create windows, all
sharing the storage session.

`window_size.py` remembers the last normal window dimensions in the library's
`window_sizes` table. Keys have separate web, device and script namespaces: web
keys are normalized hostnames (ignoring path, scheme and port while retaining
subdomains), phone keys are device IDs, and script keys are serialized launch
commands rather than temporary server URLs. Existing libraries gain the table
without changing history or bookmarks. Resizes are saved after a short debounce
and flushed on app changes, window close and application shutdown. Home and new
apps default to 420 × 780. Unchanged windows do not overwrite newer saved sizes.
GTK's default dimensions retain the normal size while maximized or fullscreen;
those states themselves are not restored across launches. Temporary audio/video
layouts never replace the full-page dimensions.

An `Adw.ApplicationWindow` contains a stack for the start page, web content,
compact audio player, and load errors. Video mode reuses the web-content child.
An `Adw.ToolbarView` places a native
`Adw.HeaderBar` with Home, Back, the page title, a site-permissions button, pin toggle,
layout menu, main menu, and close
control above the content. The home body contains a native `Gtk.StackSwitcher`
with Apps, History and Agent tabs above a shared search/address entry. Apps adds
a native plus button after the entry; the other tabs hide it so the entry
uses the full row. `app_dialogs.py` provides an adaptive, scrollable `Adw.Dialog`
for web and script apps with validation and inline errors. Saving never launches
the app, and refreshes every browser window. A shared
`Adw.Clamp` centers all Home content at a maximum width of 600 logical pixels,
with 10-pixel side margins on narrower windows. Tabs, the search entry, app tiles,
history and Agent cards share this width constraint. Apps uses one
`Gtk.FlowBox` for devices, script apps (including DeepSeek Harness), saved Home
launchers, and bookmark origins, including YouTube and Bilibili presets. The grid
uses the available Home content width, adding columns as the window grows up to
six tiles per row. Tiles share continuous rows without group headings or an
empty-device placeholder.
`script_apps.py` stores custom script tiles independently in `script_apps`,
deduplicated by normalized command arguments, port configuration and working folder.
They are rendered after the built-in DeepSeek tile and before website apps.
New script apps use managed HTTP URLs at `127.0.0.1`. An empty Port field (or zero)
selects a free TCP port per launch; a custom port is an ASCII integer from 1–65535.
The existing URL column stores an empty string for automatic allocation and the
generated loopback URL for a custom port. A derived `port` field keeps the store
interface explicit without rewriting earlier URL-based records. Those records
retain their address, path/query, scheme and fixed port. Commands use `shlex`
argument parsing without an implicit shell; a working folder is explicit and defaults to Home.
`ScriptAppHarness` extends the existing supervised server lifecycle with a
managed URL rather than trusting readiness URLs in subprocess output. Port
selection binds a temporary IPv4 socket; a process-local claim also prevents
concurrent launches from choosing a port reserved by another app's startup.
Claims are released after process cleanup, including failures and cancellation.
Only the child environment overrides `PORT`; the parent and other apps keep their
own environment. The foreground server must consume `PORT` itself. The runner
rejects occupied custom ports before launching and polls HTTP HEAD responses with
bounded timeouts, no proxy and normal HTTPS verification. Servers stay in the
foreground and have a 60-second startup deadline. Startup logs use the same
bounded in-memory redaction as DeepSeek. `BrowserApplication` caches one runner
per script ID. Window leases keep it alive until the last window leaves, and
shutdown closes every runner. Managed script pages are excluded from browsing
history and Bookmark App. Window dimensions use the working folder and argument
list as their script key. Uninstall removes the saved script record and refreshes
Home windows; open leases keep running until released, without deleting files.
Website tiles have a secondary-click gesture and keyboard context menu
(Shift+F10/Menu) exposing **Uninstall** through a native `Gtk.PopoverMenu`.
The window owns one temporary popover and detaches it before rebuilding the
grid or closing. Uninstall atomically deletes the selected exact `home_apps`
URL and records its normalized origin root in `hidden_bookmark_apps`, suppressing
bookmark/preset fallback tiles across restarts without changing source bookmarks,
history, credentials, or Chromium data. Other saved URLs remain installed.
Explicit **Bookmark App** clears the origin's suppression; adding a saved Home
app shows that URL again. All browser windows refresh after uninstall. The
agent's existing `remove_home_app` operation still removes only a saved record.
History uses a `Gtk.ListView` directly
inside a scroller, backed by `Gio.ListStore` and a list-item factory so only
visible rows need widgets. It shows all stored history addresses, including
imports, in compact full-width rows with title, URL and last visit time.
Agent aligns its prompt card with the tab strip and search entry. It keeps a
wrapped, selectable monospace prompt capped at three ellipsized lines above a
native Copy prompt button. The button copies the complete static
usage instructions to the GTK clipboard and confirms with a toast; it does not
expose page data or enable control. Below it, a virtualized `Gtk.ListView` shows
the latest 100 agent requests, newest first, with start time, command name, optional
page ID and status. `agent_activity.py` keeps this bounded list in memory for the
application session, excluding arguments, results and error text; command names
are allowlisted and page IDs must be integers. `Automation.dispatch` records
running and completed/failed requests on the GTK thread and watches pending
requests for transport timeout. Updates refresh every browser window, and new
windows receive the same records. Nothing is saved to the library or disk.
`navigation.address_to_uri` resolves input from Home, the address dialog, and
the launcher. Search text opens Google with a URL-encoded query.
Settings uses a
native `Adw.PreferencesDialog` for imports, passwords, the library, site
permissions, and shortcuts.
An Appearance switch controls the Home button. The shared application's
`show-home-button` property defaults to true, loads from the library's
`preferences` table, and updates browser headers and open Settings switches
after a successful save. Phone Home buttons bind to the same property. New windows
use the same preference; Alt+Home remains
available when the button is hidden.
Site choices live in the library's `permissions` table, keyed by a normalized
origin and one of `camera`, `microphone`, `geolocation`, or
`notifications`. Origins retain their scheme and non-default port, normalize
internationalized hosts, and reject credentials. A missing row means Ask. The
header's shield button opens the current origin's manager; Settings opens the
global list. Each row is an Ask/Allow/Block dropdown. Changes persist
immediately and reapply the origin's choices with Chromium's
`Browser.setPermission`; Reset this site sets all four to prompt, and Reset all
site permissions uses `Browser.resetPermissions`. Permission prompts are
serialized per window so requests cannot stack native dialogs, and unsupported
CEF permission types are denied. Choices are app data, not browsing history,
and are never logged.
The three-line main menu follows the layout button and offers New Window,
Refresh Page (using the existing reload action), Reload Without Cache, Bookmark App, Settings,
and an `Adw.AboutDialog` displaying the app's icon, name, version, and license.
Startup adds the package's `icons` directory to GTK's icon search path. The same
bundled SVG is used for desktop integration, so the About icon also works from
a source checkout without installing the desktop entry or updating icon caches.
Reload Without Cache uses CEF's `ReloadIgnoreCache()` to retry incomplete
pages without clearing cookies or website storage. It is disabled on Home.
Full page uses the native `view-paged-symbolic` icon.
Address, library, import, and help controls live in temporary adaptive libadwaita
dialogs. Window dimensions are defaults, not a
locked aspect ratio. Back/Forward actions track Chromium's back-forward list and
are disabled on the home page, where the Home, Back, site-permissions and page layout buttons are hidden.
The Home and site-permissions buttons appear when an app is opened. The layout
button appears when Audio player or Video player is enabled, and stays visible
in either media layout so Full page remains reachable. The `win.home` action
calls `BrowserApplication.return_home` to present an existing Home window or create
a clean one before closing the source app window. The companion's scoped
`MoveToWindow` D-Bus method copies the source frame position, monitor and workspace
to Home before the source closes. Both windows must belong to the caller.
Placement is asynchronous, with bounded retries for GTK/Mutter mapping races;
repeated Home actions share the pending target. An absent or older companion
falls back to compositor placement. A source already on Home stays open.
Reused Home windows leave maximized/fullscreen state before placement.
Closing uses the normal cleanup
for Chromium, script leases, media playback and phone transports; new Home windows
start on Apps with an empty entry and fresh WebView. Internal script-error recovery
can still show Home in place to retain its retry dialog.
While Chromium loads a page, a native loading spinner replaces Back in its
header slot: `Adw.Spinner` when available, with a `Gtk.Spinner` fallback for
libadwaita 1.5. This also covers a first navigation awaiting
engine startup. Completion, cancellation and failures restore the Back icon and
its normal availability; keyboard Back remains available when there is history.
Back is hidden on idle pages without back history. Media layouts
keep both Home and Back, with Back restoring Full page. URI notifications and load events also refresh
their state. Same-document URL changes (History API or fragments) leave media
mode and restore the browsing header, just like full-page navigation.
Chromium's user agent is unchanged.

The [disposable-profile example](../../examples/cef/README.md) runs this same
Chromium-backed main app without retaining website data.

Long-pressing Back opens a native popover with up to 20 previous Chromium navigation
entries, nearest first. Selection jumps to the existing back-forward-list item;
it does not create a new navigation entry. Navigation dismisses stale lists, and
an open history popover keeps the immersive header visible. Normal Back clicks
retain their page-history or media-layout behavior.

Chromium's `is-playing-audio` notification enables Audio player for a loaded page.
Audio mode keeps that WebView alive but unmapped and requests a
420 × 200 window; the stack measures only its visible child. Restoring preserves
the browsing size and maximized/fullscreen state. The restore action stays
available when audio stops. Navigation, home, and error transitions leave compact
mode. Page titles continue updating in either layout.
Window size changes wait for the compositor's normal-size configure before
restoring maximized/fullscreen state, so GTK retains the original normal size.
In both media layouts the header overlays the content and hides by default.
Moving to the top eight logical pixels reveals it; leaving the header hides it
after 1.5 seconds, unless a header control has keyboard focus or either menu
is open. F10 reveals and focuses the layout button. Restoring the page restores
the permanent header. Both media layouts use the native dark OSD header style.
The audio player and missing-artwork placeholder also use dark OSD styling,
independently of the system appearance and other browser windows.
Media surfaces support primary-button window dragging after GTK's drag threshold.
Native audio buttons and the video's bottom control strip retain their gestures;
ordinary video clicks are replayed by GTK when no drag occurs. The compositor
performs the move using the original input event. Back and Alt+Left leave either
media layout without navigating; normal history behavior resumes in Full page.

`window_pin.py` connects each window's pin toggle to the bundled Mini Browser Pin
GNOME Shell extension. GTK 4 provides no always-on-top request for GNOME Wayland;
the extension pairs Mutter's `make_above`/`unmake_above` with `stick`/`unstick`
to control Always on Top and Always on Visible Workspace together. Its narrow
session D-Bus interface matches both the caller's unique bus name and GTK window object path,
and only handles Mini Browser application IDs. It never matches page titles.
Replies and unicast change signals report pinned only when both compositor
settings are active. Changes to either setting through GNOME's window menu update
the toggle. The active toggle uses the native `suggested-action` accent styling
and filled pin; inactive toggles use the bundled symbolic outline icon.
Calls are asynchronous, stale replies are ignored, and mapping races receive a
bounded retry. Unavailable pinning keeps the button clickable and opens a native
setup dialog. Enable Pinning activates the installed GNOME extension; newly
installed extensions require signing out and back in before GNOME can load them.
Entering either media mode requests pinning; returning to the page restores the
previous requested setting. Manual unpinning remains available during playback.
Disabling the extension restores each original flag independently for windows it
controlled.

`audio_player.py` implements native controls. Mute uses Chromium's page-wide API.
The left cover fills the player's height with no margins; a width-for-height
layout keeps it square on resize, and `Gtk.Picture` crops artwork without
stretching it. Track title, artist (or album), website, status, and controls sit
to its right. While mapped, the player probes once a second for track changes.
Artwork comes from Media Session metadata, then video posters or page preview
metadata. A separate static script fetches images inside Chromium, retaining the
page's session, CORS, CSP, TLS, and sandbox protections. Fetches time out after
five seconds and read at most 4 MiB. The player saves no separate artwork files;
missing, invalid, or blocked covers show a native audio placeholder. Navigation
cancels artwork requests and clears track details so stale responses cannot
replace a new cover.
Play/pause uses a static asynchronous script in a separate isolated main-frame
world, with the command passed as a typed GVariant. It retains the elements it
pauses for resuming without starting unrelated paused tracks. Embedded frames,
shadow DOM, and Web Audio are not controlled by that script; mute remains usable.
Cancelled requests cannot update controls after navigation or window closure.

`video_player.py` focuses the largest playing (or largest visible) main-document
video in a window sized to its intrinsic aspect ratio, normally 640 pixels wide
and bounded by 90% of monitor width and 80% of monitor height. The header overlay
does not add height or shrink the video viewport. Metadata is awaited for up to
1.5 seconds; unavailable dimensions fall back to 640 × 360. Subsequent manual
resizing is unrestricted. A static script in its own isolated world requests DOM fullscreen on the
existing video. CEF keeps that fullscreen inside the GTK content surface;
the desktop window remains resizable. Chromium's fullscreen rules override
responsive author styles, including inline important sizes, without rewriting
the site's inline style. Exit restores the previous controls and removes the
temporary marker. The layout menu probes after page loads and when opened;
stale results are discarded. Embedded frames and shadow DOM are excluded.

Closing a window mutes and destroys its CEF browser so retained Python references
cannot leave its audio playing in another window's application process.

`chrome.py` discovers profiles and reads Chrome's bookmark JSON and read-only
history SQLite database. It filters records to HTTP(S) addresses. The native UI
runs import work on a background thread and returns results to the main loop.
No GTK object is accessed by the worker. The application stays alive until an
active import completes. Tests use generated profiles only.

`storage.py` maintains separate bookmarks and history tables keyed by URL.
Imports execute in a single transaction. History merges retain the latest visit
time and the maximum known visit count to keep repeated imports idempotent.
Local navigation increments visit counts. Folder names are retained for Chrome
bookmarks; the initial library UI is a flat searchable list. Library searches
return at most 200 matches to keep the narrow UI responsive.

`add_bookmark_app()` saves a normalized origin URL with its hostname as title;
paths, query strings and fragments are discarded, while scheme, subdomain and
non-default port remain part of the origin. `bookmarked_apps()` groups every
existing bookmark by that same root without rewriting imported records and adds
the YouTube/Bilibili presets. The window's Bookmark App action refreshes every
browser window and is disabled on Home and managed script pages. Full history
and bookmark-app queries opt out of the library dialog's 200-result limit.
History retains one row per URL with its latest visit time and visit count.

The `home_apps` table stores explicit URL/title launchers independently of history,
bookmarks and recents. The agent CLI/MCP can add, list and remove them without
loading websites. Normalized URLs deduplicate repeated additions; explicit names
and original insertion order survive visits and restarts. Home renders saved
launchers in Bookmarked apps before presets and bookmark-derived launchers,
suppressing duplicate bookmark origins. Multiple explicitly saved paths from
one origin remain separate tiles and open their exact saved URLs. Mutations
refresh every browser window's Home widgets on the main thread.

The `recent_apps` table tracks actual Mini Browser navigation separately from
imported history. It groups by origin and retains the latest URL, title, and
opening time for each site. Schema creation adds this table to existing libraries
without altering bookmark/history records. This legacy recent-site index is no
longer rendered on Home; the History tab shows all saved addresses instead.
Open home windows refresh after another window records a visit. App tiles use
native initial avatars and make no background requests to the saved websites.
Card buttons draw their own rounded hover and pressed states; the surrounding
FlowBox children remain transparent to avoid a second outline around each tile.

`deepseek.py` owns the built-in local app independently of GTK. Window tokens
lease one shared server. Its worker invokes `npx --yes --prefer-offline @deepseek-ai/dsh web
--no-open --host 127.0.0.1 --port 0` from the user's home directory. The
CLI package and metadata are reused from npm's cache when available, avoiding a
registry check on every launch; npm still downloads missing dependencies. The official
`dsh web:` announcement provides the authenticated startup URL; only loopback
root URLs are accepted. Output is continuously drained. Each tile click opens
a separate browser window immediately, displaying only a centered monospace startup
log with a typing animation and blinking end cursor until the server announces
readiness. The borderless log scrolls as it grows; animation catches up with bursts
and stops and clears on every exit from loading. The worker retains at most 80 sanitized
lines of 1,000 characters; GTK polls snapshots every 100 ms so verbose output
cannot flood the main loop. Terminal escape sequences, common credential fields
and URL query parameters are removed or redacted before display. The log is
cleared on readiness, failure or cancellation, never written to disk or forwarded
to error dialogs, and output after readiness is discarded. Managed origins are
excluded from the library so startup tokens and duplicate app tiles are not saved there.
The official MIT-licensed SVG is bundled with its source revision and license.

Returning Home, external navigation and window closure release leases. The last
release cancels even an incomplete startup. Rapid reacquisition waits for cleanup
before spawning a fresh server. Startup has a five-minute deadline; exits and
timeouts report an actionable error and permit an explicit retry. GTK callbacks
run through GLib and check the window token to discard obsolete results.
`process_supervisor.py` runs in a private process group shared with npx and its
descendants. A pipe held only by Mini Browser ties the group to the app's life,
including abrupt exits. Cleanup sends SIGTERM, allows two seconds for shutdown,
then sends SIGKILL to remaining group members. App shutdown joins the worker;
no import hold or surviving browser window keeps an unused DeepSeek server alive.
Synthetic executables test sharing, cancellation, races, timeouts, crashes,
descendant cleanup, abrupt owner death, and real Chromium navigation.

Website content stays in Chromium's sandbox. TLS errors show an error page without
an override. Unsupported external URI schemes are not launched. Browser data is
local to the account, with private app directories and a mode-0600 library
database. Password CSV import and filling use the separate encrypted vault;
Chrome's encrypted login database, cookies, and signed-in sessions are not copied.

## Phone windows

`devices.py` owns a typed phone registry in a separate `devices` table in the
private library. Android addresses deduplicate, while iPhones receive distinct
receiver identities. `AndroidDiscovery` enumerates ADB on a worker every three
seconds and dispatches only changed snapshots to the main loop. Detected phones
stay in memory, include authorization/offline status, and merge with saved entries
by hardware identity with address fallback. New Android IDs are deterministic
so saving a detected phone retains
its existing window and session. Existing saved IDs remain valid. Detection is
stopped and its subprocess reaped at shutdown; it never opens a screen. Device
entries never enter website recents or history. `BrowserWindow` renders detected
and saved phone tiles in the Apps tab's Devices group before script and bookmarked
apps; management belongs in Devices preferences. Each group has its own tile grid.
Authorized transports resolve the phone's hardware serial, cached briefly, so USB
and Wi-Fi aliases merge into one physical device. USB is preferred among equally
authorized transports; the saved tile ID and custom name are retained when the
connection changes. Saved records migrate to an optional hardware identity and
matching aliases share a tile. Opening an identified phone verifies its identity
again before starting video or accepting input. Device model names are never
used to identify or merge phones.
`BrowserApplication` reuses one
`DeviceWindow` per phone and tracks all transports, including sessions stopping
after their windows close. An application hold lasts until helper cleanup ends.

`AndroidTransport` uses the bundled scrcpy 5.0.1 server in raw H.264 mode, with
an explicitly selected ADB device, a random abstract socket, and an
ephemeral loopback forwarding port. A private, uniquely named server copy is
pushed to `/data/local/tmp` and removed on shutdown. Server metadata framing is
disabled; the application does not implement scrcpy's version-dependent control
protocol. Basic taps, swipes, keys, and ASCII text use a bounded input queue of
ADB commands with each remote argument quoted. Input requires the active
streaming window. Coordinates exclude letterboxing and scale to the phone's
native display size, swapping axes when the stream rotates. The bundled binary's
SHA-256 is verified before use and its exact version is passed to the server.
It is distributed offline with its Apache-2.0 license and provenance. Neither a
desktop scrcpy executable nor a runtime download is needed. The RPM requires ADB.

`IPhoneTransport` runs UxPlay 1.73+ with video RTP forwarding to an already bound
loopback receiver. It disables audio and uses a per-tile empty configuration,
stable receiver MAC, random four-digit pairing code, and mode-0600 client register
under a mode-0700 directory. On later sessions, the receiver restricts connections
to the first registered device. This is AirPlay viewing with an unlocked phone;
desktop control and Apple's locked-phone mirroring are not implemented.

`DeviceVideo` keeps encoded frames in memory, using bounded `appsrc` buffering
for Android and `udpsrc`/RTP for iPhone, H.264 parsing/decoding, and
`gtk4paintablesink` in a `Gtk.Picture`. It renders directly in the native window.
GTK updates run on the main loop; transport work runs on background threads.
Generation checks discard stale callbacks after disconnect/reconnect. Stream
loss, decoder errors, close, screen lock, and sleep clear the image; lock/sleep
requires explicit reconnect. Helper output is discarded instead of logging
device metadata or input. Removal waits for helper shutdown before deleting
pairing data. Synthetic helper tests cover the native video path without phones.

See [phone mirroring research](phone-mirroring.md) for platform constraints and
the deferred iPhone Bluetooth/AssistiveTouch control option.

## Passwords

`vault.py` is independent of GTK and desktop keyring services. New vaults and
credential changes require exactly six ASCII digits, including leading zeroes.
Existing master passwords remain valid when unlocking older vaults; the file
format and KDF are unchanged. Version 1 of
`passwords.vault` is `MINIVLT1` (8 bytes), a random salt (16), a random AES-GCM
nonce (12), and an authenticated ciphertext/tag. The fixed KDF is Argon2id with
64 MiB memory, three passes, four lanes, and a 32-byte output, using PyCA
cryptography 44+. AES-256-GCM authenticates the magic/version and salt as AAD.
Every write has a fresh nonce. The encrypted JSON contains all credential
metadata and secrets. No password index is stored in the library database.

Writes use a private temporary ciphertext file, fsync, atomic replacement, and
directory fsync. An advisory file lock serializes writers. A SHA-256 revision
check rejects stale sessions instead of overwriting another process's changes.
Failed writes preserve the in-memory record set; no plaintext files are created.
CSV input is limited to 16 MiB and 10,000 rows. Credentials merge by exact
canonical origin and username. Chrome's ASCII/punycode hostnames are accepted;
Unicode hostnames are skipped to avoid ambiguous IDNA2003 mappings.

Native password dialogs perform derivation/import/writes on a worker thread.
The app drops key and record references after five minutes without vault use,
on manual lock, last-window closure, shutdown, and available screen-lock/sleep
notifications. Pending fill requests are cancelled and password dialogs close.
The derived key bytearray is overwritten on lock, but Python and Chromium do not
guarantee that all temporary plaintext copies are erased. Protection is at rest.

`autofill.py` provides static main-frame scripts executed in an isolated Chromium
world, retained by a document-start UserScript registered on each content
manager. Credentials arrive as typed GVariant arguments, never script source.
The UI requires an explicit action and account selection. Python navigation
generation, exact origin, and an isolated per-document token prevent stale
selections from filling a different page. The script checks origin and token
again immediately before filling. It fills one visible password field, excludes
new-password/confirmation forms and cross-origin form actions, and never submits.
Only HTTPS or loopback HTTP are allowed; imported HTTP entries are not promoted
to HTTPS credentials. No user-content message handler exposes the vault to pages.

References: <https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/>
and <https://cryptography.io/en/latest/hazmat/primitives/aead/>.

## System packaging

`scripts/build-rpm` archives the source tree and invokes the spec in `packaging/`.
It excludes generated bytecode, build outputs, screenshots, and all personal
browser data. The architecture-specific RPM installs a private Python package in `/usr/share/mini-browser`
and CEF under `/usr/lib64/mini-browser/cef` (the target library directory),
with an isolated Python launcher in `/usr/bin/mini-browser`, plus the desktop entry,
icon, license, and documentation. Runtime dependencies are declared in the spec.
Package checks run the deterministic tests and validate the desktop entry.

## Chromium boundary

`chromium.py` is the GTK engine adapter; `native/cef/bridge.cc` implements ABI 5.
One application-wide CEF context provides persistent cookies and storage under
`chromium/default` in the data directory. HTTP cache is under `chromium` in the
cache directory. Earlier WebKit data is left intact; website sessions are not
converted, so upgrades require signing in again. Library and vault formats are
unchanged. Chromium uses its system proxy mode, including GNOME proxy and PAC
settings. Authentication prompts are native dialogs and are not persisted.

CEF runs windowless on its headless platform, while GTK owns Wayland windows,
headers, dialogs, input methods and clipboard integration. CEF's supported
multi-threaded message loop runs on its dedicated UI thread with a private GLib
context. Browser operations are marshaled synchronously to that thread; caller
strings remain valid until each operation returns. The ctypes calls release the
Python GIL while waiting. Manual pumping on GTK's thread caused Chromium's GLib
source to poll continuously with a zero timeout between pumps, consuming a CPU
core even on static pages. External message pumping alone did not remove that
source's behavior.

Paint callbacks copy at most one pending BGRA frame per view under a lock and
schedule GTK presentation on the main thread. Protocol events and results also
reach GTK through idle callbacks. Hidden views, compact audio layouts and
compositor-suspended windows notify CEF through `WasHidden`, stopping layout and
painting and allowing Chromium's background throttling. They release native
framebuffer storage, pending frames and GTK textures. Showing a view invalidates
its surface to request fresh pixels without reloading the document. Audio stays
alive; hiding a page does not discard its DOM or JavaScript heap, and active-site
memory use still depends on the page. Repeated allocations at the same dimensions
do not notify CEF of a resize. Shutdown closes browsers on CEF's UI thread and
waits for their close callbacks before stopping CEF on the application thread.
The subprocess
links directly to libcef before libc, required by Chromium's Linux sandbox.
Unprivileged user namespaces, seccomp and no-new-privileges are required. The
setuid helper is unused; neither `--no-sandbox` nor TLS bypass flags are used.

The adapter uses CEF's in-process DevTools API; it opens no debugging port.
Explicit `--agent` activation adds a private same-user Unix control socket.
`control.py` handles bounded socket I/O on worker threads; `automation.py`
dispatches application and DevTools operations through GLib on the main thread.
`agent_cli.py` and the native `mcp.py` stdio server share `agent_tools.py` schemas
and use that socket without loading GTK. The application holds its lifecycle
while control is enabled, until Quit or `quit_browser`; shutdown closes the
listener and removes its socket. Accessibility snapshots bind element uids to
the view, navigation generation and latest snapshot. Native snapshots redact
editable values and block password dialogs; native button input and a bounded
action allowlist support UI tests. There is no vault control interface, remote
debugging port, Chrome extension dependency or CDP event stream. See the
[agent guide](../user/agents.md) for protocol limits and compatibility boundaries.
Document-start scripts run in named isolated worlds. Typed function calls use
a unique execution-context ID and typed protocol argument values, with both
navigation generation and cancellation checked. Passwords never become script
source. Console output and CEF logging are disabled; protocol results are capped
at 8 MiB. Chromium's own credential-saving service is disabled. Camera, microphone,
geolocation and notifications choices are managed by Mini Browser rather
than blindly granted or denied. The native bridge forwards
permission prompts and media-access requests; unknown permission types and
desktop capture remain denied. Stored choices apply to the shared Chromium
context through the DevTools `Browser.setPermission` method whenever an origin
is loaded and after a choice changes. `Browser.resetPermissions` clears an
explicit reset-all. A native prompt is used when Chromium asks for a decision,
with a checked-by-default "Remember this choice" option. DRM/Widevine is not
bundled. The stock official CEF SDK lacks H.264/AAC. The optional x86_64
`scripts/build-cef --codecs` build uses Arch Linux's codec-enabled CEF, matching
the official SDK's exact CEF and Chromium versions. `native/cef/codecs.json`
pins the SDK, runtime and private library packages by SHA-256. All packages are
verified before extraction; the runtime's matching resources replace the SDK
resources together. No foreign packages are installed into the operating system.
The bridge and helper use an origin-relative transitive RPATH so private codec
dependencies resolve without changing the GTK process's library environment.
Chromium's namespace sandbox and certificate checks remain enabled.
The encoded-media smoke test decodes generated H.264/AAC and VP9/Opus samples,
including fragmented MP4 through MediaSource; canvas-generated video alone does
not exercise a decoder. User installation fingerprints every runtime file,
including resources reached through development symlinks, and retains immutable
old runtime directories when replacing the active runtime symlink.
Fedora RPMs select this codec build on Fedora 45+ x86_64 (glibc 2.44), with
private OpenH264 included so decoding does not depend on a system codec stub.
Older Fedora builds and ARM retain the stock SDK. Source RPMs contain every
pinned input; native-library loading is checked in each buildroot before packaging.

`page_context.py` builds contextual choices and agent prompts without GTK.
CEF's context-menu handler supplies hit-test URLs, selected text, edit flags and
native command IDs to `page_menu.py`, which presents a scrollable GTK popover.
CEF retains only its custom-menu continuation, not context parameter/model
references. Commands return through that continuation to preserve the originating
frame for editing; desktop Copy/Cut use the permitted selection snapshot and
Paste uses GTK's clipboard with the adapter's navigation guard. Password fields
cannot contribute selection text. Dismissal, navigation, renderer termination
and window closure release the continuation and popover. Page archives and PDFs
use typed DevTools calls and asynchronous private GIO writes through a native
destination picker. Printing hands a private temporary PDF to GTK's system print
dialog (GTK 4.14+) and removes it after completion or cancellation. Source viewing
uses the current main document's HTML. Explicit context-menu window actions
bypass the automatic same-origin popup retargeting policy.
Send to Agent copies CLI/MCP read instructions plus JSON page context to the
clipboard; it neither enables control nor sends a message. Page-provided text
never becomes shell command text.

`extensions.py` owns a GTK-independent `chrome_extensions` registry in the library
database. Paths are canonical and unique; names, versions and enabled choices
persist independently of history and bookmarks. Registration reads bounded MV3
manifest metadata on a worker thread without modifying or copying source files.
`extension_page.py` provides a separate `ExtensionsWindow` with native folder
selection, switches, removal and a restart notice. The browser's three-line main
menu invokes `win.extensions`; `BrowserApplication.open_extensions` creates or
focuses one shared manager. It owns no WebView, remains open when browsing windows
close, and supports Ctrl+W. Closing it clears the application's reference so it can
be reopened. Registration callbacks refresh whichever manager is currently open,
including when the original manager closed while work was in progress.
Invalid/unavailable enabled folders
are reported and skipped at startup. Chromium validates the complete manifest.
Before initializing CEF, the application passes validated enabled paths through
bridge ABI 8. The browser process uses `load-extension` and
`disable-extensions-except` with that list; with no enabled extensions it keeps
`disable-extensions`. Changes require a complete application restart. No debugging
port, unsafe extension debugging flag or browser-session CDP channel is exposed.
CEF's Chrome bootstrap supports extension content scripts, service workers,
runtime messaging, storage and declarative network rules with the shared context;
the Alloy windowless views do not provide Chrome's toolbar or full tab/window UI.
At startup, `extension_ui` resolves IDs from the canonical Linux path or manifest
public key using Chromium's SHA-256/a–p ID algorithm. Declared action popup paths
must resolve to files inside the selected extension directory. The application
retains a startup snapshot of extension origins and popup URLs. The manager's
Open popup action is available only for loaded, enabled extensions with a popup.
`extension_popup.py` renders each extension page in an independent native window
and reuses open windows by URL. It uses a `chromium.View` restricted at creation
to that extension origin, without BrowserWindow scripts, vault filling, history,
or an agent-visible webview. Ordinary browsing views still reject extension URLs.
Native navigation checks also block cross-extension popup navigation and route
HTTP(S) links to normal browser windows. Chromium owns the extension scheme and
its privileged runtime/storage APIs; no host file server or API emulation is used.
The default CEF client captures extension URLs requested by Chrome windows and
forwards them to GTK, then closes the unrendered Chrome window. A bounded delayed
close handles empty windows; shutdown drains all close callbacks. GTK opens only
origins from the startup snapshot whose registry entries remain enabled. These
are recreated pages, so Chrome window IDs, opener handles, active-tab association
and dynamic toolbar actions are not preserved. The adapter also propagates engine
close events to the native popup window and releases its framebuffers and input.
The app does not offer Web Store/CRX installation. Source
folders remain in place; removing a registry entry does not erase extension data.
The isolated extension smoke test verifies execution and independent disabling
across real process restarts using two generated extensions and a loopback site.
It also covers popup rendering, background messaging, native input, notification
window requests, reuse/reopening, engine-initiated close and extension-origin guards.

`page_clipboard.py` connects page Copy buttons to the native GTK clipboard.
Windowless Chromium's clipboard does not provide that desktop integration.
A main-world `writeText` adapter passes text to an isolated world, which checks
transient user activation and invokes Chromium's original Clipboard API first.
Successful writes use a named DevTools binding to reach GTK. The host additionally
checks the main frame, exact secure origin, document identity, navigation
generation, mapped/focused view, active window and a recent native mouse/key
event. Writes are limited to 1 MiB of UTF-8 text. Replies use typed protocol
arguments, and clipboard contents are never logged or inserted into script
source. No desktop clipboard reads are exposed to pages.
Popup views additionally permit their exact extension origin, explicitly bound
when the view is created. Ordinary web views cannot opt into extension origins.
The same main-frame, document, focus and recent-user-input checks still apply;
only text writes cross into GTK. HTTPS and loopback HTTP support this path: loopback is already a
[potentially trustworthy origin](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy),
so the local DeepSeek server needs no certificate or security override.

`native/cef/release.json` pins official SDK release metadata. `scripts/build-cef`
verifies the archive before extraction and builds with CMake/Ninja. There are no
runtime downloads. Shipping a Chromium update requires rebuilding and distributing
a new runtime; Mini Browser does not inherit Chrome's automatic updater.

The native header and dialogs retain GTK accessibility. The windowless web
surface does not yet export Chromium's accessibility tree to AT-SPI.
