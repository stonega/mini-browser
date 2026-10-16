# Architecture

Mini Browser is one native desktop application. Python and PyGObject bind GTK 4,
libadwaita (1.5+), and WebKitGTK 6.0 (2.40+). These are system packages, with no
web UI or JavaScript build toolchain. GTK 4.12+ is required by the CSS API.

`BrowserApplication` owns the application actions, private data paths, SQLite
library, and a shared persistent `WebKit.NetworkSession`. `BrowserWindow` owns
one WebView and defaults to 420 × 780 logical pixels. Related WebViews reuse
WebKit's opener relationship and storage session, so popup navigation and POST
requests remain under WebKit's control.

An `Adw.ApplicationWindow` contains a stack for the start page, web content,
compact audio player, and load errors. Video mode reuses the web-content child.
An `Adw.ToolbarView` places a native
`Adw.HeaderBar` with the page title, Settings gear, layout menu, and close
control above the content. The home body contains
only a search/address entry and up to six recent-site tiles. Settings uses a
native `Adw.PreferencesDialog` for imports, passwords, the library, and shortcuts.
Address, library, import, and help controls live in temporary adaptive libadwaita
dialogs. Window dimensions are defaults, not a
locked aspect ratio. WebKit's user agent is unchanged.

WebKit's `is-playing-audio` notification enables Audio player for a loaded page.
Audio mode keeps that WebView alive but unmapped and requests a
360 × 200 window; the stack measures only its visible child. Restoring preserves
the browsing size and maximized/fullscreen state. The restore action stays
available when audio stops. Navigation, home, and error transitions leave compact
mode. Page titles continue updating in either layout.
Window size changes wait for the compositor's normal-size configure before
restoring maximized/fullscreen state, so GTK retains the original normal size.

`audio_player.py` implements native controls. Mute uses WebKit's page-wide API.
Play/pause uses a static asynchronous script in a separate isolated main-frame
world, with the command passed as a typed GVariant. It retains the elements it
pauses for resuming without starting unrelated paused tracks. Embedded frames,
shadow DOM, and Web Audio are not controlled by that script; mute remains usable.
Cancelled requests cannot update controls after navigation or window closure.

`video_player.py` focuses the largest playing (or largest visible) main-document
video in a 640 × 406 window. A static script in its own isolated world uses the
Popover API to put the existing video in the top layer without moving its DOM
node or requesting desktop fullscreen. Temporary styles make it fill the WebView
and expose native media controls. Exit restores the controls and overridden
styles, retaining unrelated inline changes made by the website. The layout menu
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

Website content stays in WebKit's sandbox. TLS errors show an error page without
an override. Unsupported external URI schemes are not launched. Browser data is
local to the account, with private app directories and a mode-0600 library
database. Password CSV import and filling use the separate encrypted vault;
Chrome's encrypted login database, cookies, and signed-in sessions are not copied.

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
