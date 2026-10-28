# Mini Browser

A native GNOME web browser with a small footprint on your screen.

- A native GNOME header bar with Home, Back, the page title, and close button, plus web content.
- Links requesting a new tab or window open in the current window; Ctrl+N opens another.
- A resizable **420 × 780** portrait window by default.
- Compact audio and video layouts, available from the layout icon beside Close.
- A pin button before the layout icon, with always-on-top enabled by default in media layouts.
- A home page with Apps/History tabs, phone and script apps, bookmarks, and complete saved history.
- A DeepSeek launcher with its official icon and managed local-server lifetime.
- Native Android mirroring with basic input, and authenticated iPhone AirPlay viewing.
- Automatic Android phone tiles, with scrcpy's phone-side helper included in the app.
- Chrome/Chromium bookmark and history import, plus bookmark HTML exports.
- Chrome password CSV import into an app-local encrypted vault, with login filling.
- Shared, persistent website sessions across Mini Browser windows.
- Native dialogs for addresses, the searchable library, import, and downloads.
- Opt-in agent CLI and native Codex MCP tools for Chromium and GTK UI testing.

See the [release notes](docs/user/releases.md) for the latest changes.

Agent control: start with `mini-browser --agent`, then use
`mini-browser agent --help`. Register native MCP with
`codex mcp add mini_browser -- mini-browser mcp`.
See [agent commands, Codex setup and UI testing](docs/user/agents.md).
Save a Home tile with `mini-browser agent add-app https://example.com --title 'Example'`.

Built with Python, GTK 4, libadwaita, and sandboxed Chromium (CEF).
Version 0.4.0 adds opt-in agent CLI and native Codex MCP control, a unified
Home Apps grid with persistent app tiles, and animated script startup logs.
H.264/AAC playback remains available on Fedora 45+ x86_64.

## Fedora RPM

Install from [COPR](https://copr.fedorainfracloud.org/coprs/stonegate/mini-browser/):

```sh
sudo dnf copr enable stonegate/mini-browser
sudo dnf install mini-browser
```

The repository targets Fedora 43–45 and Rawhide on x86_64 and aarch64.
Restart existing browser windows after upgrading.

Or build and install the native system package locally:

```sh
./scripts/build-rpm
sudo dnf install ./dist/mini-browser-0.4.0-1.fc45.x86_64.rpm
/usr/bin/mini-browser
```

Build tools: `rpm-build`, `desktop-file-utils`, `python3-cryptography` (44+),
`cmake`, `ninja-build`, `gcc-c++`, and `ruff`. The binary and source RPMs are written to `dist/`. Installing the
package adds Mini Browser to GNOME's launcher and leaves personal browser data
in your account. Restart existing browser windows after an upgrade.

## Run

On Fedora, install the native runtime packages if needed:

```sh
sudo dnf install python3-gobject gtk4 libadwaita python3-cryptography cmake ninja-build gcc-c++
```

From this directory:

```sh
./scripts/build-cef
./scripts/run
./scripts/run https://www.gnome.org
./scripts/run 'weather in Taipei'
```

Each argument opens its own window. Home has Apps and History tabs above a
search/address field. Apps groups devices, script apps such as DeepSeek Harness,
and bookmarked apps with YouTube and Bilibili presets. History lists all saved
addresses in compact full-width rows. Choose Bookmark App in the main menu to
save the current website's root URL as an app. DeepSeek starts on
demand using `npx @deepseek-ai/dsh web` and stops when its last window leaves;
Node.js and npm must be on your desktop's PATH. The header bar's three-line menu, after the
layout button, contains Settings for devices, imports, passwords, and your library, plus
About Mini Browser with the app version and license. While browsing, the header
bar sits above the page. The narrow window lets responsive websites use their compact
layout; the browser keeps Chromium's normal desktop user agent.

Pinning uses the bundled Mini Browser Pin GNOME Shell extension. After installing
the RPM or running `scripts/install`, log out and back in, then run
`gnome-extensions enable mini-browser-pin@io.github.stone`. See the
[usage guide](docs/user/usage.md#media-layouts) for pin and player behavior.

## Controls

| Action | Shortcut |
| --- | --- |
| Open an address or search | Ctrl+L |
| Home page | Alt+Home |
| Settings | Ctrl+, |
| Reveal media title bar | F10 |
| New window | Ctrl+N |
| Close window | Ctrl+W |
| Back / forward | Alt+Left / Alt+Right |
| Reload / stop | Ctrl+R or F5 / Escape |
| Bookmarks and history | Ctrl+B or Ctrl+H |
| Bookmark current website as an app | Ctrl+D |
| Import Chrome data | Ctrl+Shift+I |
| Manage passwords / import CSV | Ctrl+Shift+P |
| Fill a saved login | Ctrl+Shift+L |
| Lock password vault | Ctrl+Shift+K |
| Zoom in / out / reset | Ctrl++ / Ctrl+− / Ctrl+0 |
| Show shortcuts | F1 or Ctrl+? |
| Quit all windows | Ctrl+Q |

Drag the native header bar to move the window. Resize from the window edges as usual.

Click the layout icon before the three-line menu to choose **Full page**,
**Audio player**, or **Video player**. Audio mode becomes available while the page
plays audio; video mode becomes available when a supported video is present.
Both keep the same page loaded and hide the title bar until you move to the top
edge or press F10. Video mode initially matches the video's aspect ratio and
fits within the monitor. Choose **Full page** or press Escape to restore
the previous window size. See [the user guide](docs/user/usage.md#media-layouts)
for controls and supported media.

## Import from Chrome

Use **Settings → Import bookmarks and history** or **Ctrl+Shift+I**. Select a detected
profile, or choose a profile folder containing `Bookmarks` and/or `History`.
Choose the data types and click **Import selected data**. Open imported items
with **Ctrl+B**. Imports merge by URL and can be repeated.

Chrome, Chrome Beta/Dev, Chromium, and the usual Chrome/Chromium Flatpak profile
locations are detected. You can also export bookmarks from Chrome as HTML and
choose **Import bookmarks from HTML…**.

Bookmarks and browsing history import directly from the profile (up to 50,000
recent addresses). Passwords use a separate Chrome CSV export. Cookies,
extensions, autofill addresses, passkeys, and signed-in sessions are not imported.
Sign in to websites again; Mini Browser then keeps its own cookies. No Chrome
files are changed. See [the user guide](docs/user/usage.md).

## Password vault

Open **Settings → Passwords** or press **Ctrl+Shift+P**. Create a
**six-digit PIN**, then import Chrome's password CSV. Leading zeroes are supported.
The vault
uses Argon2id and AES-256-GCM through `cryptography` 44+; it does not use GNOME
Keyring. Website names, usernames, and passwords are encrypted together.

On a login page, press **Ctrl+Shift+L** and choose the saved account. Filling is
limited to the exact website origin over HTTPS (or HTTP on loopback for local
sites). It fills the main-page login form and leaves submission to you. Embedded
frames, registration forms, and forms sending to another origin are excluded.

The vault locks after five minutes without password use, when the last window
closes, and on GNOME screen lock or system sleep when those D-Bus signals are
available. Use **Ctrl+Shift+K** to lock it manually. Delete the exported CSV
after import: that file contains readable passwords. Keep an encrypted vault
backup and remember your PIN; there is no PIN recovery. Existing vaults still
accept their previous master password; use **Change PIN** to switch to six digits.

## Alternative user installation

```sh
./scripts/install
```

This copies the app, desktop entry, and icon into your user account. It requires
no root access and does not change your default browser. Re-run after updates.
Use the RPM for a system installation. An earlier user desktop entry can shadow
the system entry; see [the setup notes](docs/implementation/setup.md).

## Develop and verify

Install Ruff (`sudo dnf install ruff`), then run:

```sh
./scripts/check
./scripts/smoke                  # starts an isolated virtual GNOME desktop
./scripts/smoke screenshots.png  # optional capture of the test app window
```

The smoke test uses a local HTTP server and a temporary data directory. It checks
the actual Chromium renderer, portrait viewport, popup windows, shortcuts, history,
bookmarks, native dialogs, and media layouts with a generated WAV and canvas video.
It never imports a real Chrome profile.
GNOME Shell and `dbus-run-session` are required. Test windows run on a private
headless Wayland display with silent audio, without opening on your desktop.

Some YouTube videos and livestreams require H.264/AAC. Fedora 45 and Rawhide
x86_64 RPMs include a codec-enabled runtime; older Fedora versions and ARM use
the stock CEF engine without those codecs. See the
[codec-enabled x86_64 build](docs/implementation/setup.md#h264aac-web-video-x86_64)
and its encoded-video smoke check (`./scripts/smoke --test=codecs`).

See [architecture](docs/design/architecture.md) and [setup notes](docs/implementation/setup.md).
