# Using Mini Browser

Launch Mini Browser from GNOME, or run `./scripts/run` in the project. Enter a
website or search in the home page's input bar, then press Enter or its arrow.
The home page also shows up to six recently opened sites. A native GNOME header
bar shows the page title, Settings gear, and close button. Open the address
dialog with Ctrl+L to inspect the
current URL or navigate elsewhere. Use F1 to see all keyboard shortcuts.

Recent tiles group pages from the same website and reopen its most recently
visited page. They track sites opened in Mini Browser; importing Chrome history
does not add tiles. Return home with Alt+Home. Other home windows refresh when
you open a site in another Mini Browser window.

Open Settings with the header's gear button or Ctrl+,. It contains browser data
import, password CSV import, bookmarks/history, saved passwords, and shortcuts.

Ctrl+N opens another portrait window. A website link that requests a new window
also gets a separate window. Ordinary links navigate in their current window.
All windows share Mini Browser's own login sessions. Windows can be freely
resized; the default portrait size does not force a mobile user agent.

Drag the header bar to move the window. Close it with its close button or Ctrl+W.

## Import bookmarks and history

1. Choose Settings → Import bookmarks and history, or press Ctrl+Shift+I.
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

Cookies, autofill addresses, extensions, passkeys, and Chrome's signed-in sessions
are not imported. Passwords use the separate CSV workflow below.

## Import and use passwords

1. In Chrome, open Google Password Manager → Settings → Export passwords, and
   save the CSV locally.
2. In Mini Browser, choose Settings → Import passwords, or open Settings →
   Passwords (Ctrl+Shift+P) to manage the vault before importing.
3. Create and confirm a PIN containing exactly six digits. Leading zeroes are
   supported. Later sessions ask you to unlock the vault with this PIN. Existing
   vaults also accept their previous master password.
4. Choose Import Chrome password CSV and select the exported file. Import results
   report added, updated, and skipped entries. Re-importing updates the password
   for an existing website/account without duplicating it.
5. Delete the CSV when finished: it holds readable passwords.
6. Open a website's login page, press Ctrl+Shift+L, and choose your account.
   Review the filled form and click the website's sign-in button yourself.

Credentials are matched by exact scheme, hostname, and port. An account imported
for `https://example.org` is not filled on `https://login.example.org`, another
port, or `http://example.org`. HTTPS and local loopback HTTP sites support filling.
Filling acts on visible login fields in the main page, not embedded frames or
registration/password-change forms. Forms sending to another website are
excluded. Some custom or multi-step login forms need manual entry.

Use the Passwords dialog to search saved accounts, delete individual entries, or
change your PIN. Ctrl+Shift+K locks the vault. It also locks after
five minutes without password use, when the last window closes, and on GNOME
screen lock or system sleep when session notifications are available.

The vault is independent of GNOME Keyring. There is no recovery for a forgotten
PIN or master password. To back it up, lock it, then copy `passwords.vault` to your
backup location. Restoring that encrypted file requires its original master
PIN or master password. Changing the PIN does not change existing backup copies.

## Local data

Mini Browser keeps the library, its own cookies, and WebKit site data under
`~/.local/share/mini-browser`. Caches are under `~/.cache/mini-browser`.
`XDG_DATA_HOME` and `XDG_CACHE_HOME` are respected when set. Chrome source
profiles are never rewritten by the importer.

Saved logins are encrypted in `~/.local/share/mini-browser/passwords.vault`.
This protects the file at rest; an unlocked vault and filled web forms contain
plaintext in memory. Closing or locking the vault drops its in-memory records
and key references. Python cannot guarantee erasing every temporary memory copy.
Already-filled web forms retain their values when the vault locks. The PIN or
master password is never saved to disk.

To reset browser data, first close every Mini Browser window and wait for any
import or download to finish. Remove `library.sqlite3` to clear bookmarks and
history. Remove `cookies.sqlite3` and the `webkit` data directory to reset
website sessions and site storage. Keep the `app` subdirectory if you installed
the application with `scripts/install`.
