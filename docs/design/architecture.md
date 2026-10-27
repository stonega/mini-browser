# Architecture

Mini Browser is one native desktop application. Python and PyGObject bind GTK 4,
libadwaita (1.5+), and Chromium through CEF 156. GTK bindings are system packages;
the pinned CEF runtime is bundled. There is no
web UI or JavaScript build toolchain. GTK 4.12+ is required by the CSS API.

`BrowserApplication` owns the application actions, private data paths, SQLite
library, and a shared persistent `chromium.Session`. `BrowserWindow` owns
one WebView and defaults to 420 × 780 logical pixels. Links requesting another
tab/window navigate that same WebView. A document-start script in an isolated
world retargets links and submitted forms while preserving native navigation and
POST bodies; CEF lifespan/request handlers redirect other HTTP(S) GET popup
requests and refuse new WebViews. `window.open()` returns no popup handle;
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
`Adw.HeaderBar` with Home, Back, the page title, pin toggle, layout menu, main menu, and close
control above the content. The home body contains
only a search/address entry, a Devices group for detected and saved phones, and
an Apps group with a built-in DeepSeek Harness tile and up to six recent-site
tiles. Empty device groups are hidden.
Settings uses a
native `Adw.PreferencesDialog` for imports, passwords, the library, and shortcuts.
The three-line main menu follows the layout button and offers New Window,
Refresh Page (using the existing reload action), Reload Without Cache, Settings,
and an `Adw.AboutDialog` displaying the app's name, version, and license.
Reload Without Cache uses CEF's `ReloadIgnoreCache()` to retry incomplete
pages without clearing cookies or website storage. It is disabled on Home.
Full page uses the native `view-paged-symbolic` icon.
Address, library, import, and help controls live in temporary adaptive libadwaita
dialogs. Window dimensions are defaults, not a
locked aspect ratio. Back/Forward actions track Chromium's back-forward list and
are disabled on the home page, where the Back button is hidden. Home has its own
button in every header. Back is hidden on pages without back history. Media layouts
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

The `recent_apps` table tracks actual Mini Browser navigation separately from
imported history. It groups by origin and retains the latest URL, title, and
opening time for each site. Schema creation adds this table to existing libraries
without altering bookmark/history records. Home tiles are sorted by most recent
use; open home windows refresh after another window records a visit. Tiles use
native initial avatars and make no background requests to the saved websites.
Card buttons draw their own rounded hover and pressed states; the surrounding
FlowBox children remain transparent to avoid a second outline around each tile.

`deepseek.py` owns the built-in local app independently of GTK. Window tokens
lease one shared server. Its worker invokes `npx --yes @deepseek-ai/dsh web
--no-open --host 127.0.0.1 --port 0` from the user's home directory. The official
`dsh web:` announcement provides the authenticated startup URL; only loopback
root URLs are accepted. Output is bounded in memory, continuously drained, and
never logged or forwarded to error dialogs. Managed origins are excluded from
the library so startup tokens and duplicate app tiles are not saved there.
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
and saved phone tiles in a separate Devices group before the Apps website group;
management belongs in Devices preferences. Each group has its own tile grid and
visibility state, so devices do not suppress the empty website hint.
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

`chromium.py` is the GTK engine adapter; `native/cef/bridge.cc` implements ABI 3.
One application-wide CEF context provides persistent cookies and storage under
`chromium/default` in the data directory. HTTP cache is under `chromium` in the
cache directory. Earlier WebKit data is left intact; website sessions are not
converted, so upgrades require signing in again. Library and vault formats are
unchanged. Chromium uses its system proxy mode, including GNOME proxy and PAC
settings. Authentication prompts are native dialogs and are not persisted.

CEF runs windowless on its headless platform, while GTK owns Wayland windows,
headers, dialogs, input methods and clipboard integration. Paint callbacks retain
at most one pending BGRA frame per view. A GLib timer pumps CEF on the GTK main
thread; callbacks are dispatched outside the native callback stack. The subprocess
links directly to libcef before libc, required by Chromium's Linux sandbox.
Unprivileged user namespaces, seccomp and no-new-privileges are required. The
setuid helper is unused; neither `--no-sandbox` nor TLS bypass flags are used.

The adapter uses CEF's in-process DevTools API; it opens no debugging port.
Document-start scripts run in named isolated worlds. Typed function calls use
a unique execution-context ID and typed protocol argument values, with both
navigation generation and cancellation checked. Passwords never become script
source. Console output and CEF logging are disabled; protocol results are capped
at 8 MiB. Chromium's own credential-saving service is disabled. Camera, microphone
and geolocation permission prompts are currently denied. DRM/Widevine and
proprietary codecs are not bundled; HTML video support depends on CEF's codecs.

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
HTTPS and loopback HTTP support this path: loopback is already a
[potentially trustworthy origin](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy),
so the local DeepSeek server needs no certificate or security override.

`native/cef/release.json` pins official SDK release metadata. `scripts/build-cef`
verifies the archive before extraction and builds with CMake/Ninja. There are no
runtime downloads. Shipping a Chromium update requires rebuilding and distributing
a new runtime; Mini Browser does not inherit Chrome's automatic updater.

The native header and dialogs retain GTK accessibility. The windowless web
surface does not yet export Chromium's accessibility tree to AT-SPI.
