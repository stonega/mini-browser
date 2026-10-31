# Using Mini Browser

Launch Mini Browser from GNOME, or run `./scripts/run` in the project. Enter a
website or search in the home page's input bar, then press Enter or its arrow.
Searches use Google by default, including searches from Ctrl+L and the command line.
The Home tabs, search bar and cards share a centered content area up to 600 pixels
wide, shrinking to fit narrower windows.
Home has **Apps**, **History**, and **Agent** tabs. Apps shows devices, script apps
(including DeepSeek Harness), YouTube and Bilibili presets, saved Home apps,
and bookmarked websites together in one grid without group titles. The grid fills
the available width and adds columns as you widen the window, up to six apps per
row. History shows every saved history address, including imported Chrome history,
newest first. Each compact row spans the Home content area and shows the page
title, full URL, and last visit time; long text is shortened to fit, with details
available on hover. Select a row to
reopen that exact page. Repeat visits update the address's time and visit count.
A native GNOME header
bar includes a Home button in opened apps, followed by Back when available, the page
title, site permissions (shield), pin, layout menu, three-line main menu, and close
button. The Home, site permissions, and page layout buttons
stay hidden on Home, including its History tab.
Home (or Alt+Home) closes the current app window and activates an existing Home
window. If none is open, it creates a fresh Home window with an empty search bar
and the Apps tab selected. Using Home while already on Home keeps that window.
This also applies to phone windows; Phone Home in their menu controls the phone.
Back returns to the previous page and is hidden when there is no previous page.
While a page loads, a spinner replaces Back, including on the first page. When
loading finishes, is stopped with Escape, or fails, Back returns if there is history.
Open the address
dialog with Ctrl+L to inspect the
current URL or navigate elsewhere. Use F1 to see all keyboard shortcuts.

On **Apps**, click **+** beside the search bar to add an app. Choose **Web app**,
enter a full HTTP(S) URL and an optional name, then click **Add**. The tile opens
that exact URL, including its path. Adding an app saves it without opening it.
The button is hidden on History and Agent.

Choose **Script app** to save a name, launch command, working folder, and optional
port. Leave **Port** empty (or enter `0`) to choose a free port each time the server
starts. Enter **1–65535** to use a fixed custom port. The command receives that
number in its `PORT` environment variable, and Mini Browser opens
`http://127.0.0.1:PORT/`. Your server must read `PORT` and listen on that port;
no launch arguments are added automatically. The selected value overrides any
inherited `PORT` for this command without changing your desktop environment.

Use the folder containing your web app. Commands accept quoted arguments; use a
shell script when you need pipes, environment assignments, or shell expansion.
For example, a wrapper script can run `python3 -m http.server "$PORT" --bind 127.0.0.1`.
The working folder defaults to your home directory and accepts an absolute path
or `~`.

Script commands start only when you open their tiles. Use a foreground server
that keeps running and serves the managed local URL. Mini Browser shows the startup
log and waits up to 60 seconds for an HTTP response before opening the page. An
already occupied custom port produces an error; choose another port or stop the other
server. Multiple windows of the same script app share one server. Returning
Home, navigating to another origin, or closing its last window stops the command
and its child processes. Closing Mini Browser also stops its servers. Saved
script tiles survive restarts; their pages do not add history or website tiles.
Apps saved with a local URL by earlier versions retain that URL and use its port
for `PORT`; their HTTPS certificate checks remain enabled. Re-add an app with an
empty Port field to switch it to automatic allocation.

Choose **Bookmark App** from the three-line menu, or press Ctrl+D, to add the
current website to Apps. Bookmarks use URL apps: bookmarking
`https://apc.example.com/sdacsd/sdca` saves and opens `https://apc.example.com/`.
Paths, queries, and fragments are removed; subdomains, scheme, and non-default
ports are retained. Repeated bookmarks of that site share one tile. Imported
bookmarks also appear grouped by site, while their original page links stay in
the library. Visiting a site adds history rather than an app tile.
Return home with Alt+Home. Other home windows refresh as you visit or bookmark sites.

Right-click a website or saved script tile in **Apps** and choose **Uninstall** to remove it.
With the tile focused, Shift+F10 or the Menu key opens the same menu. This works
for saved apps, bookmarked sites, and the YouTube/Bilibili presets. Removal
updates every Home window and survives restarts, without deleting bookmarks,
history, passwords, cookies, or website data. A bookmark or preset tile for the
same origin stays hidden; other saved URLs at that origin remain available.
Use **Bookmark App** on the website, or save it again through agent control, to
restore a tile. Uninstalling a script tile leaves its open instances running
until their windows close; it does not delete the script's files. Phone and
DeepSeek Harness tiles keep their existing management.

Home's **Agent** tab shows a three-line preview of the monospace **Agent prompt**
for controlling the browser, adding apps and testing the UI. Choose **Copy prompt**
to copy the complete instructions into your agent chat.
Below it, **Agent actions** lists the latest 100 CLI/MCP requests in this browser
session, newest first, with their time, page ID when available, and status.
Records update across windows and clear when the application exits. They contain
command names and status only, without request contents, results or error details.

Open Settings from the three-line main menu after the layout button, or press
Ctrl+,. It contains browser data import, password CSV import, bookmarks/history,
saved passwords, Devices, site permissions, and shortcuts.
Choose **New Window** in the same menu (or press Ctrl+N) to open another portrait window.
Choose **Refresh Page** (or press Ctrl+R or F5) to reload the current page.
If a page stays partly loaded, choose **Reload Without Cache** (Ctrl+Shift+R or
Ctrl+F5) to fetch its resources again. This keeps cookies and saved sign-ins;
it does not reset the website's stored data.
Choose **About Mini Browser** in the same menu to see the app version and license.

## Site permissions

While a website is open, the shield button before the pin button opens **Site
permissions** for the current origin. **Settings → Site permissions** opens the
same manager for every site with a saved choice. Camera, microphone, location,
and notifications each have an **Ask**, **Allow**, or **Block** choice. Ask is the default; Allow and Block are saved per origin, including the
scheme and any non-default port, and apply immediately.

When a page requests one of these permissions, a native prompt offers **Allow**,
**Block**, or **Not now**. Leave **Remember this choice for this site** selected
to save Allow or Block; clear it, or choose **Not now**, to answer only this
request. Unsupported permission types and screen sharing remain blocked.
**Reset this site** returns that origin's choices to Ask; **Reset all site
permissions** returns every site to Ask.

Website links that request a new tab or window stay in the current window when
they point to the same origin (scheme, hostname and port), including different
paths on that site. Links to another origin open a new Mini Browser window and
leave the original page open. Ordinary links and submitted forms continue in
the current window. Scripted popup flows that need a separate window handle or build
a blank popup are unsupported.
All windows share Mini Browser's own login sessions. Windows can be freely
resized; the default portrait size does not force a mobile user agent.

Mini Browser remembers each app's last window size across restarts. Website
sizes are shared by hostname: `sds.example.com/first` and
`sds.example.com/second` share a size, while another subdomain has its own.
HTTP/HTTPS and port changes share the same hostname size. Phones remember their
size by device ID, and script apps such as DeepSeek Harness by their launch
command, even when the local server changes port. Home and apps without a saved
size use 420 × 780. Audio/video layouts preserve the saved Full page size;
maximized and fullscreen windows remember their normal dimensions.

Drag the header bar to move the window. Close it with its close button or Ctrl+W.
Press and hold Back with the mouse or a touchscreen to list up to 20 earlier
pages from this window, newest first. Select a page to jump directly to it.

## DeepSeek Harness

Open **DeepSeek Harness** under **Apps** to start its local web server
and open it inside Mini Browser. The tile uses DeepSeek's official whale icon.
Node.js and npm's `npx` must be available on the desktop session's PATH. On first
launch, npx downloads the official package; starting it can take a few minutes.
The tile immediately opens a separate window with a centered monospace startup
log, a typing animation, and a blinking cursor at the end. Longer logs scroll
to the newest output. When the server is ready, the app opens in that same
window. Startup output stays in memory only; common credential fields and URL
query parameters are redacted. Closing the loading window cancels its launch.
Configure models and choose a workspace in DeepSeek's own interface.

DeepSeek windows share one server. Returning Home, navigating to another site,
or closing a DeepSeek window releases that window's session. The server and its
child processes stop when the last DeepSeek window leaves, including during
startup. Closing Mini Browser also stops them; a separate supervisor handles
abrupt app exits. Startup failures offer **Try again**. Closing a server stops
any agent tasks it is running, so let tasks finish before leaving its last window.

DeepSeek keeps its own settings and sessions in its usual location (`~/.dsh`,
or `DSH_HOME` when set). Mini Browser starts it from your home directory and does
not delete that data. DeepSeek pages do not create website apps or history entries.

Text Copy buttons work with GNOME's clipboard, including DeepSeek's local HTTP
page. Keep the page active and click its Copy button, then paste into another
app normally. HTTPS and loopback HTTP support this; the local server does not
need HTTPS. Pages cannot read the desktop clipboard through this integration.

## Phone apps

Connected Android phones appear in **Apps** automatically within a few seconds,
alongside script apps and websites. Manage phones in **Settings → Devices**.
Enable USB debugging in Android Developer options, connect by USB, and approve
this computer on the phone. The tile shows **Connected**, **Allow USB debugging**,
or **Offline**. Open it to show the phone in a native window; opening it again
focuses that same window. Detection never opens the screen automatically.
USB and wireless ADB connections to the same physical phone share one tile;
an authorized USB connection is preferred. Separate phones of the same model
remain separate devices.

There is no separate scrcpy installation: Mini Browser includes its Android
helper. The RPM automatically installs ADB; source or user-only installations
need ADB on PATH (see the [setup guide](../implementation/setup.md#phone-mirroring-dependencies)).
An already paired wireless ADB device also appears; Mini Browser does not enable
wireless debugging or pair wireless ADB for you.

Automatically detected tiles disappear when their phones disconnect. To keep a
tile, open **Settings → Devices**, then **Save phone** under Connected Android
phones. Saved phones remain on Home with a Disconnected status and can be renamed
or removed in Settings. Removing a saved Android phone does not hide it while
it is still connected. You can also add a device manually with its ADB address.

Add an iPhone through **Settings → Devices → Add iPhone**. Its saved tile stays
on Home. Removing it stops the receiver and deletes its local pairing data after
the receiver has stopped.

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
The active pin uses the theme's accent color and a filled icon; an inactive pin
uses an outlined icon.

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
to the top edge to reveal Home, Back, pin, layout, main menu, and Close, or press F10 to reveal
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
compact 420 × 200 native player while the same page keeps playing.
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

Choose **Video player** to focus a main-page video with Chromium's playback controls.
Its initial window proportions match the video's own aspect ratio, usually at
640 pixels wide; tall videos shrink to fit the monitor. You can still resize it
freely. When video dimensions are unavailable, the default is 640 × 360 (16:9).
The playing video is preferred; otherwise the
largest visible video is selected. It stays attached to its page, with its
original styling and controls restored when you leave video mode.
Responsive players such as YouTube stay fitted to the video window as it resizes,
with the surrounding page and its controls hidden behind a black backdrop.
Videos in embedded frames, shadow DOM, or existing website popovers are not supported.
Video mode uses DOM fullscreen inside the app's content area; the GTK window stays resizable.

Choose **Full page** or press Escape to return to the previous size and
maximized/fullscreen state. This remains available after media pauses or ends.
Navigating, including URL changes within the same page, reloading, returning
home, or a page error restores the browsing layout automatically. Back is hidden
on the home page. Closing the player closes that page's window.

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

Mini Browser keeps the library, its own cookies, and Chromium site data under
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
history, saved devices and remembered window sizes. Remove the `chromium` data
directory to reset website sessions and site storage. Keep the `app` subdirectory if you installed
the application with `scripts/install`.


The Chromium migration preserves bookmarks, history, and the encrypted password
vault. Existing WebKit cookies and site data are left in place, but cannot be
used by Chromium; sign in to websites again after upgrading. Earlier data remains
in `webkit` and `cookies.sqlite3` until you remove it yourself. Network requests
use the system proxy settings. The stock engine lacks H.264/AAC, which some
YouTube videos and livestreams require. Fedora 45 and Rawhide x86_64 RPMs include
these codecs; Fedora 43/44 and ARM use the stock engine. A codec-enabled x86_64
user build requires glibc 2.44 or newer; see the
[setup guide](../implementation/setup.md#h264aac-web-video-x86_64).
DRM-protected video requires Widevine, which is not included.

## Agent control

Start with `mini-browser --agent` to enable the local JSON CLI and native Codex
MCP server. Use `mini-browser agent --help` for commands and `mini-browser mcp`
as the MCP stdio command. See [agent control and UI testing](agents.md) for
the full command reference, Codex configuration, and isolated UI tests.

To add an app to Home, run
`mini-browser agent add-app https://example.com --title 'Example'` while agent
control is enabled. Saved tiles stay on Home across restarts. Use
`mini-browser agent apps` to list them and `mini-browser agent remove-app URL`
to remove one. See [saved Home apps](agents.md#saved-home-apps) for details.
