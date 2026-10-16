# Using Mini Browser

Launch Mini Browser from GNOME, or run `./scripts/run` in the project. Enter a
website or search on the start page. When the website loads, only a close button
occupies the browser surface. Open the address dialog with Ctrl+L to inspect the
current URL or navigate elsewhere. Use F1 to see all keyboard shortcuts.

Ctrl+N opens another portrait window. A website link that requests a new window
also gets a separate window. Ordinary links navigate in their current window.
All windows share Mini Browser's own login sessions. Windows can be freely
resized; the default portrait size does not force a mobile user agent.

To move a window, hold Super and drag its content using GNOME's normal gesture.
To close it, use the floating close button or Ctrl+W.

## Import bookmarks and history

1. Choose Import from Chrome on the start page, or press Ctrl+Shift+I.
2. Select your Chrome profile. For a custom location, choose the folder that
   contains the files named `Bookmarks` and `History` (often `Default` or
   `Profile 1`, inside `~/.config/google-chrome`).
3. Select Bookmarks, Browsing history, or both, then start the import.
4. Read the result. If Chrome has locked a source file, close Chrome and retry.
5. Use Ctrl+B to browse or search the library. Select a result to open it in the
   current window. Use Ctrl+N first if you want to keep the current page open.

Repeated imports do not create duplicate URLs. Import updates bookmark names
and folders. History keeps the newer visit title and time, and the greater visit
count. The 50,000 most recent history addresses are considered per import.
Chrome internal pages and bookmarklets are omitted.

For HTML imports, export bookmarks from Chrome's bookmark manager. Choose
Import bookmarks from HTML in Mini Browser and select that file. This imports
HTTP(S) bookmark links and titles. HTML folder structure is not retained.

Passwords, cookies, autofill, extensions, and Chrome's signed-in sessions are
not imported in this version. Log in to websites in Mini Browser as needed.

## Local data

Mini Browser keeps the library, its own cookies, and WebKit site data under
`~/.local/share/mini-browser`. Caches are under `~/.cache/mini-browser`.
`XDG_DATA_HOME` and `XDG_CACHE_HOME` are respected when set. Chrome source
profiles are never rewritten by the importer.

To reset browser data, first close every Mini Browser window and wait for any
import or download to finish. Remove `library.sqlite3` to clear bookmarks and
history. Remove `cookies.sqlite3` and the `webkit` data directory to reset
website sessions and site storage. Keep the `app` subdirectory if you installed
the application with `scripts/install`.
