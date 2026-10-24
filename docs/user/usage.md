# Using Mini Browser

Launch Mini Browser from GNOME, or run `./scripts/run` in the project. Enter a
website or search in the home page's input bar, then press Enter or its arrow.
The home page also shows saved phones and up to six recently opened sites. A native GNOME header
bar shows Back, the page title, pin, layout icon, three-line main menu, and close button.
Back returns to the previous page and is disabled when there is no previous page.
Open the address
dialog with Ctrl+L to inspect the
current URL or navigate elsewhere. Use F1 to see all keyboard shortcuts.

Recent tiles group pages from the same website and reopen its most recently
visited page. They track sites opened in Mini Browser; importing Chrome history
does not add tiles. Return home with Alt+Home. Other home windows refresh when
you open a site in another Mini Browser window.

Open Settings from the three-line main menu after the layout button, or press
Ctrl+,. It contains browser data import, password CSV import, bookmarks/history,
saved passwords, Devices, and shortcuts.
Choose **New Window** in the same menu (or press Ctrl+N) to open another portrait window.
Choose **About Mini Browser** in the same menu to see the app version and license.

Website links that request a new tab or window open in the current window, as do
ordinary links. Scripted popup flows that need a separate window handle or build
a blank popup are unsupported.
All windows share Mini Browser's own login sessions. Windows can be freely
resized; the default portrait size does not force a mobile user agent.

Drag the header bar to move the window. Close it with its close button or Ctrl+W.
Press and hold Back with the mouse or a touchscreen to list up to 20 earlier
pages from this window, newest first. Select a page to jump directly to it.

## Phone apps

Open **Settings → Devices** to add an Android phone or iPhone. Its app tile stays
on Home beside your recent sites. Open the tile to show the phone in a native
window; opening it again focuses that same window. Rename or remove phones in
Devices. Removing a phone stops its session and deletes its local AirPlay pairing
data after the receiver has stopped.

For Android, install ADB and scrcpy with its matching server (see the
[setup guide](../implementation/setup.md#phone-mirroring-dependencies)). Enable
USB debugging in Android Developer options, connect by USB, and approve this
computer on the phone. Choose **Find connected phones**, select the phone, and
add it. An already paired wireless ADB address also works; Mini Browser does not
enable wireless debugging or pair wireless ADB for you.

Click to tap, drag to swipe or long-press, and scroll to swipe. Focus the phone
image to type ASCII text or use Backspace, Enter, Tab, Delete, and the arrow keys.
Right-click sends Android Back. The phone window's menu offers Back, Home, and
the app switcher. Input goes only to the selected phone while its window is
active. Use the phone keyboard for other languages. ADB input can have noticeable
latency; true multitouch and keyboard modifier combinations are not supported.

For iPhone, install UxPlay 1.73 or newer. Open the tile, keep both devices on the
same local network, and choose the displayed receiver name in **iPhone Control
Center → Screen Mirroring**. Enter the four-digit pairing code if asked. Keep
the phone unlocked and use it to control the screen. Paired clients are checked
against this tile's private registration file; subsequent receiver sessions are
restricted to the first paired phone. Add a separate tile for another iPhone.
Discovery or network isolation/firewall settings can prevent a connection.

This release mirrors video only. iPhone desktop input, phone audio forwarding,
DRM-protected iPhone video, and Apple's locked-phone iPhone Mirroring behavior
are not supported. Live streams are not recorded. Phone names and Android
connection identifiers are kept in the private app library, independently of
web history; AirPlay public pairing records are stored under the app's private
`devices` directory.

Use the phone window's menu to **Disconnect** or **Reconnect**; Escape disconnects
and Ctrl+R/F5 reconnects. Closing the window stops its helper and clears the
screen. Screen lock and sleep disconnect all phones when session notifications
are available; select Reconnect explicitly after returning. An interrupted
iPhone stream clears the old image and waits for Screen Mirroring to resume.

## Media layouts

Click the layout icon before the main menu to choose **Full page**,
**Audio player**, or **Video player**. Unavailable options are disabled; the menu
checks for videos again each time you open it. Full page uses a page-shaped icon.

The pin button immediately before the layout button turns on both GNOME's
**Always on Top** and **Always on Visible Workspace**, keeping the window above
other windows and visible as you switch workspaces. Unpinning turns both off.
Audio and video modes turn pinning on automatically; click it to unpin the
player. Returning to Full page restores the pin setting from before media mode.
Each window has its own pin setting.

Pinning requires the bundled **Mini Browser Pin** GNOME Shell extension on GNOME
45–51. The RPM and `scripts/install` install it. After installation, log out and
back in, then enable it once:

```sh
gnome-extensions enable mini-browser-pin@io.github.stone
```

If pinning is unavailable, click the pin button and choose **Enable Pinning**.
The setup dialog explains when signing out and back in is needed after installation.
Browsing and media playback remain available. The extension only accepts requests for a Mini Browser
window owned by the calling application; it does not read page content.

Audio and video modes hide the title bar for an immersive view. Move the pointer
to the top edge to reveal Back, pin, layout, main menu, and Close, or press F10 to reveal
and focus the layout button. The bar overlays the player and hides after you
move away; it stays visible while its controls have keyboard focus or either menu
is open. Full page restores the usual title bar.
Escape also restores Full page.
Back (or Alt+Left) also returns to Full page while a player is open, keeping the
same page and playback position. In Full page mode it returns through history.

Drag the audio artwork, track details, or video surface to move the player window.
Playback buttons remain clickable, and the video's bottom control strip stays
available for seeking and volume changes.

Start audio on a page, then choose **Audio player**. It switches the window to a
compact 360 × 200 native player while the same page keeps playing.
The player and its revealed title bar use a dark appearance, including when the
desktop uses a light theme. Returning to Full page restores the usual title bar.

The player shows a square cover flush against the left edge, filling the window's
height. Track title, artist or album (when supplied by the website), website,
playback status, and play/pause and mute buttons appear on the right. Artwork and
track details update as tracks change. Covers use the website's Media Session
artwork, video poster, or page preview image; unavailable images or images blocked
by the website's image-fetching rules show an audio icon instead. Non-square
artwork is cropped to fill the square without stretching.

Play/pause controls standard audio and video elements in the main page;
resuming restarts only the elements paused by the player, or the first element
if none were paused by it. Embedded frames, custom Web Audio players, and media
inside shadow DOM require the website's own play/pause controls. Mute works for
the whole page, including those players. Websites can also restrict background
playback or reject playback requests.

Choose **Video player** to focus a main-page video with WebKit's playback controls.
Its initial window proportions match the video's own aspect ratio, usually at
640 pixels wide; tall videos shrink to fit the monitor. You can still resize it
freely. When video dimensions are unavailable, the default is 640 × 360 (16:9).
The playing video is preferred; otherwise the
largest visible video is selected. It stays attached to its page, with its
original styling and controls restored when you leave video mode.
Responsive players such as YouTube stay fitted to the video window as it resizes,
with the surrounding page and its controls hidden behind a black backdrop.
Videos in embedded frames, shadow DOM, or existing website popovers are not supported.
Video mode requires WebKit's Popover API; older versions leave this option disabled.

Choose **Full page** or press Escape to return to the previous size and
maximized/fullscreen state. This remains available after media pauses or ends.
Navigating, including URL changes within the same page, reloading, returning
home, or a page error restores the browsing layout and its Back button
automatically. Closing the player closes that page's window.

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
