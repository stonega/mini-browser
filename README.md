# Mini Browser

A native GNOME web browser with a small footprint on your screen.

- Web content and a floating close button. No toolbar or tabs while browsing.
- One page per window, including links that open a new window.
- A resizable **420 × 780** portrait window by default.
- Chrome/Chromium bookmark and history import, plus bookmark HTML exports.
- Shared, persistent website sessions across Mini Browser windows.
- Native dialogs for addresses, the searchable library, import, and downloads.

Built with Python, GTK 4, libadwaita, and WebKitGTK 6.0.

## Run

On Fedora, install the native runtime packages if needed:

```sh
sudo dnf install python3-gobject gtk4 libadwaita webkitgtk6.0
```

From this directory:

```sh
./scripts/run
./scripts/run https://www.gnome.org
./scripts/run 'weather in Taipei'
```

Each argument opens its own window. The start page lets you enter an address,
import Chrome data, or open your library. Once a website is open, only the close
button remains. The narrow window lets responsive websites use their compact
layout; the browser keeps WebKit's normal user agent.

## Controls

| Action | Shortcut |
| --- | --- |
| Open an address or search | Ctrl+L |
| New window | Ctrl+N |
| Close window | Ctrl+W |
| Back / forward | Alt+Left / Alt+Right |
| Reload / stop | Ctrl+R or F5 / Escape |
| Bookmarks and history | Ctrl+B or Ctrl+H |
| Bookmark current page | Ctrl+D |
| Import Chrome data | Ctrl+Shift+I |
| Zoom in / out / reset | Ctrl++ / Ctrl+− / Ctrl+0 |
| Show shortcuts | F1 or Ctrl+? |
| Quit all windows | Ctrl+Q |

Use GNOME's Super+drag gesture to move a window by its web content. Resize from
the window edges as usual.

## Import from Chrome

Use **Import from Chrome** on the start page or **Ctrl+Shift+I**. Select a detected
profile, or choose a profile folder containing `Bookmarks` and/or `History`.
Choose the data types and click **Import selected data**. Open imported items
with **Ctrl+B**. Imports merge by URL and can be repeated.

Chrome, Chrome Beta/Dev, Chromium, and the usual Chrome/Chromium Flatpak profile
locations are detected. You can also export bookmarks from Chrome as HTML and
choose **Import bookmarks from HTML…**.

This version imports **bookmarks and browsing history** (up to 50,000 recent
addresses per import). Passwords, cookies, extensions, autofill, and signed-in
Chrome sessions are not imported. Sign in to websites again; Mini Browser then
keeps its own cookies. No Chrome files are changed. See [the user guide](docs/user/usage.md).

## Install in the GNOME launcher

```sh
./scripts/install
```

This copies the app, desktop entry, and icon into your user account. It requires
no root access and does not change your default browser. Re-run after updates.

## Develop and verify

Install Ruff (`sudo dnf install ruff`), then run:

```sh
./scripts/check
./scripts/smoke                  # requires a graphical session
./scripts/smoke screenshots.png  # optional capture of the test app window
```

The smoke test uses a local HTTP server and a temporary data directory. It checks
the actual WebKit renderer, portrait viewport, popup windows, shortcuts, history,
bookmarks, and native dialogs. It never imports a real Chrome profile.

See [architecture](docs/design/architecture.md) and [setup notes](docs/implementation/setup.md).
