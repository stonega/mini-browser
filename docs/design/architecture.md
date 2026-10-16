# Architecture

Mini Browser is one native desktop application. Python and PyGObject bind GTK 4,
libadwaita (1.5+), and WebKitGTK 6.0 (2.40+). These are system packages, with no
web UI or JavaScript build toolchain. GTK 4.12+ is required by the CSS API.

`BrowserApplication` owns the application actions, private data paths, SQLite
library, and a shared persistent `WebKit.NetworkSession`. `BrowserWindow` owns
one WebView and defaults to 420 × 780 logical pixels. Related WebViews reuse
WebKit's opener relationship and storage session, so popup navigation and POST
requests remain under WebKit's control.

An `Adw.ApplicationWindow` contains a stack for the start page, web content, and
load errors. A floating close control overlays the top right corner. There is no
header bar in the main window. Address, library, import, and help controls live
in temporary adaptive libadwaita dialogs. Window dimensions are defaults, not a
locked aspect ratio. WebKit's user agent is unchanged.

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

Website content stays in WebKit's sandbox. TLS errors show an error page without
an override. Unsupported external URI schemes are not launched. Browser data is
local to the account, with private app directories and a mode-0600 library
database. Chrome credentials and encrypted sessions are outside the importer.
