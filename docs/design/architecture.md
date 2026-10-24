# Architecture

Mini Browser is one native desktop application. Python and PyGObject bind GTK 4,
libadwaita (1.5+), and WebKitGTK 6.0 (2.40+). These are system packages, with no
web UI or JavaScript build toolchain. GTK 4.12+ is required by the CSS API.

`BrowserApplication` owns the application actions, private data paths, SQLite
library, and a shared persistent `WebKit.NetworkSession`. `BrowserWindow` owns
one WebView and defaults to 420 × 780 logical pixels. Links requesting another
tab/window navigate that same WebView. A document-start script in an isolated
world retargets links and submitted forms while preserving native navigation and
POST bodies; WebKit policy/create handlers redirect other HTTP(S) GET popup
requests and refuse new WebViews. `window.open()` returns no popup handle;
blank popups and programmatic POST popups that bypass submission events are
unsupported. Explicit application new-window actions still create windows, all
sharing the storage session.

An `Adw.ApplicationWindow` contains a stack for the start page, web content,
compact audio player, and load errors. Video mode reuses the web-content child.
An `Adw.ToolbarView` places a native
`Adw.HeaderBar` with Back, the page title, pin toggle, layout menu, main menu, and close
control above the content. The home body contains
only a search/address entry and up to six recent-site tiles. Settings uses a
native `Adw.PreferencesDialog` for imports, passwords, the library, and shortcuts.
The three-line main menu follows the layout button and offers New Window,
Settings, and an `Adw.AboutDialog` displaying the app's name, version, and license.
Full page uses the native `view-paged-symbolic` icon.
Address, library, import, and help controls live in temporary adaptive libadwaita
dialogs. Window dimensions are defaults, not a
locked aspect ratio. Back/Forward actions track WebKit's back-forward list and
are disabled on the home page. URI notifications and load events also refresh
their state. Same-document URL changes (History API or fragments) leave media
mode and restore the browsing header, just like full-page navigation.
WebKit's user agent is unchanged.

Long-pressing Back opens a native popover with up to 20 previous WebKit navigation
entries, nearest first. Selection jumps to the existing back-forward-list item;
it does not create a new navigation entry. Navigation dismisses stale lists, and
an open history popover keeps the immersive header visible. Normal Back clicks
retain their page-history or media-layout behavior.

WebKit's `is-playing-audio` notification enables Audio player for a loaded page.
Audio mode keeps that WebView alive but unmapped and requests a
360 × 200 window; the stack measures only its visible child. Restoring preserves
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
the toggle. Calls are asynchronous, stale replies are ignored, and mapping races receive a
bounded retry. Unavailable pinning keeps the button clickable and opens a native
setup dialog. Enable Pinning activates the installed GNOME extension; newly
installed extensions require signing out and back in before GNOME can load them.
Entering either media mode requests pinning; returning to the page restores the
previous requested setting. Manual unpinning remains available during playback.
Disabling the extension restores each original flag independently for windows it
controlled.

`audio_player.py` implements native controls. Mute uses WebKit's page-wide API.
The left cover fills the player's height with no margins; a width-for-height
layout keeps it square on resize, and `Gtk.Picture` crops artwork without
stretching it. Track title, artist (or album), website, status, and controls sit
to its right. While mapped, the player probes once a second for track changes.
Artwork comes from Media Session metadata, then video posters or page preview
metadata. A separate static script fetches images inside WebKit, retaining the
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
resizing is unrestricted. A static script in its own isolated world uses the
Popover API to put the existing video in the top layer without moving its DOM
node or requesting desktop fullscreen. A user-origin stylesheet scoped to the
selected open popover makes it fill the WebView and gives it an opaque black
backdrop, while the script exposes native media controls. Its important rules
take precedence over inline sizing, including important declarations rewritten
by responsive players such as YouTube on resize. The site's inline styles are
never modified. Exit removes the temporary marker and restores the controls,
leaving the site's latest layout intact. The layout menu
probes for supported videos when opened and after page loads; stale results are
discarded. Embedded frames, shadow DOM, and existing website popovers are excluded.
The option stays disabled when WebKit lacks the Popover API.

Closing a window mutes and unloads its WebView so retained Python references
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

Website content stays in WebKit's sandbox. TLS errors show an error page without
an override. Unsupported external URI schemes are not launched. Browser data is
local to the account, with private app directories and a mode-0600 library
database. Password CSV import and filling use the separate encrypted vault;
Chrome's encrypted login database, cookies, and signed-in sessions are not copied.

## Phone windows

`devices.py` owns a typed phone registry in a separate `devices` table in the
private library. Android addresses deduplicate, while iPhones receive distinct
receiver identities. Device entries never enter website recents or history.
`BrowserWindow` renders saved phone tiles before recent sites; management belongs
to the native Devices preferences dialog. `BrowserApplication` reuses one
`DeviceWindow` per phone and tracks all transports, including sessions stopping
after their windows close. An application hold lasts until helper cleanup ends.

`AndroidTransport` uses the installed scrcpy release's own server in raw H.264
mode, with an explicitly selected ADB device, a random abstract socket, and an
ephemeral loopback forwarding port. A private, uniquely named server copy is
pushed to `/data/local/tmp` and removed on shutdown. Server metadata framing is
disabled; the application does not implement scrcpy's version-dependent control
protocol. Basic taps, swipes, keys, and ASCII text use a bounded input queue of
ADB commands with each remote argument quoted. Input requires the active
streaming window. Coordinates exclude letterboxing and scale to the phone's
native display size, swapping axes when the stream rotates.

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
The derived key bytearray is overwritten on lock, but Python and WebKit do not
guarantee that all temporary plaintext copies are erased. Protection is at rest.

`autofill.py` provides static main-frame scripts executed in an isolated WebKit
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
browser data. The noarch RPM installs a private package in `/usr/share/mini-browser`
and an isolated Python launcher in `/usr/bin/mini-browser`, plus the desktop entry,
icon, license, and documentation. Runtime dependencies are declared in the spec.
Package checks run the deterministic tests and validate the desktop entry.
