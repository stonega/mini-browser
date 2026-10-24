# Setup and verification

Use `/usr/bin/python3` with distribution-provided PyGObject, GTK 4.12+,
libadwaita 1.5+, WebKitGTK 6.0 (2.40+), and cryptography 44+ with Argon2id support.
A pip-only virtual environment will
not usually see native GI bindings. No Bun/npm dependencies are needed for this
native desktop app.

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita webkitgtk6.0 python3-cryptography ruff
./scripts/run
```

Development commands:

```sh
./scripts/check
ruff format src tests scripts/install
./scripts/smoke
./scripts/smoke --test=devices  # focused phone UI checks, still in the isolated desktop
./scripts/build-rpm
```

## Phone mirroring dependencies

Phone windows additionally use GStreamer's introspection bindings, H.264 parser
and decoder, RTP plugins, and the native GTK 4 paintable sink. On Fedora:

```sh
sudo dnf install gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good \
  gstreamer1-plugins-bad-free gstreamer1-plugin-gtk4 gstreamer1-plugin-openh264
```

Android requires `adb` (Fedora's `android-tools`) and scrcpy major version 3, 4,
or 5 with the server from the exact same release. Follow the
[upstream Linux installation instructions](https://github.com/Genymobile/scrcpy/blob/master/doc/linux.md)
for scrcpy; package/repository availability varies. The app searches beside the
scrcpy executable and in `share/scrcpy/scrcpy-server` under its installation
prefix, then `/usr/share/scrcpy` and `/usr/local/share/scrcpy`. Set
`SCRCPY_SERVER_PATH` if the matching server is elsewhere. No device server is
downloaded automatically. USB debugging authorization is required; wireless ADB
must already be paired outside the app.

iPhone viewing requires [UxPlay](https://github.com/FDH2/UxPlay) 1.73 or newer
with `-vrtp` support (`uxplay` is packaged by Fedora). It also needs working
local-network service discovery, usually Avahi for packaged versions, and
firewall access for AirPlay/mDNS. The app does not change firewall rules or
system services. Each receiver uses its own empty configuration, stable local
identity, private client register, and displayed pairing code. Video is forwarded
only over loopback into GTK, without a second display window. Audio is disabled.
Missing dependencies produce a reconnectable error in the phone window.

The phone smoke test requires `openh264enc` and `openh264dec` (provided by
`gstreamer1-plugin-openh264`), along with the plugins above. It creates fake ADB,
scrcpy, and UxPlay executables inside a temporary directory and puts only these
ahead of the system tools. It exercises real H.264 decoding and RTP delivery,
Settings, tile persistence, input mapping, reconnect, session-lock cleanup, and
iPhone stream loss/recovery in the isolated desktop. It never accesses real
phones. Optional captures include `-android.png` and `-iphone.png`.

## Verification environment

`check` runs standard-library unit tests, Ruff lint and format checks, and Python
byte compilation. `smoke` starts a disposable GNOME Shell with a 1280 × 1024
virtual monitor, a private Wayland display and D-Bus session, and temporary XDG
directories. It requires `gnome-shell`, `dbus-run-session` (`dbus-daemon` on
Fedora), `at-spi2-core`, and the GStreamer GI typelib. No active graphical session
is needed. The runner starts a separate accessibility bus and registry daemon
before WebKit launches, avoiding private-bus systemd activation failures.
Test windows stay off the user's desktop. Navigation checks cover same-window
target links, script popups, native POST submissions, Back, and explicit new windows.
The runner copies and enables the bundled pin extension only in the disposable
desktop. Media checks verify manual pinning, pinning by default in both player
layouts, and restoration of the page's prior pin setting.
Separate pin checks cover independent windows, rejection of unrelated D-Bus
callers, compositor state notifications, rapid requests, and extension restarts.
The runner also loads a test-only pointer extension in that disposable desktop.
It only accepts owned MediaSmoke windows, injects real pointer drags/clicks, and
checks compositor window positions. Media checks cover draggable audio/video
surfaces, clickable playback controls, the video control strip, normal page
interaction, and Back restoring Full page without navigation. This helper is
never installed with the application.
Audio sinks are disabled only for the
test processes, using GStreamer's clocked fake sink for silent playback checks.
The GNOME process and test process groups are stopped and temporary desktop data
is removed after the run, including failed tests. Each test also uses a separate
application ID, temporary browser data, and a loopback HTTP server.
Pass an optional PNG filename to capture only the test application window.
The media smoke test serves a generated WAV and canvas video. It verifies WebKit
audio detection, square edge-to-edge artwork at default and resized dimensions,
track metadata and cover updates, missing-cover and page-preview fallbacks,
continued compact playback, mute, play/pause, size and window-state
restoration, navigation, and the Web Audio fallback. Video checks cover live
frames, native controls, intrinsic landscape/portrait sizing, delayed metadata,
immersive header reveal and keyboard/menu access, audio/video switching,
responsive site sizing rewrites, opaque video backdrops, restored DOM styling,
and closing an active player while another window stays open. It saves
additional `-audio.png` and `-video.png` captures when requested.
The password smoke test also creates a temporary vault, imports a synthetic CSV
through the native dialog, checks failed and successful unlocks, then fills a
local WebKit login form. It checks isolated-world setters, exact-site account
selection, origin/document/form guards, and manual locking. Neither test command
imports private Chrome data or uses the system keyring.

## Fedora system package

```sh
sudo dnf install rpm-build desktop-file-utils python3-cryptography ruff
./scripts/build-rpm
sudo dnf install ./dist/mini-browser-0.2.3-1.fc45.noarch.rpm
/usr/bin/mini-browser
```

The build uses `build/rpm` and writes the binary and source RPMs into `dist/`.
The binary package installs `/usr/bin/mini-browser`, modules under
`/usr/share/mini-browser`, the desktop entry, icon, license, and docs. DNF resolves
the declared GTK, WebKitGTK, PyGObject, and cryptography runtime dependencies.
No personal bookmarks, history, cookies, or vault files are packaged or removed.

The package also installs the Mini Browser Pin extension for GNOME Shell 45–51.
Log out and back in after first installation, then run
`gnome-extensions enable mini-browser-pin@io.github.stone` to enable always-on-top
controls. The application detects extension availability without a restart.

If migrating from `scripts/install`, move the old user desktop entry
`~/.local/share/applications/io.github.stone.MiniBrowser.desktop` aside so GNOME
uses the packaged entry. Replace `~/.local/bin/mini-browser` with a symlink to
`/usr/bin/mini-browser` or remove that old wrapper. Keep the `mini-browser` data
directory and `passwords.vault`. Restart Mini Browser to load the installed code.

## Alternative user installation

`scripts/install` copies the Python package under
`$XDG_DATA_HOME/mini-browser/app`, adds `~/.local/bin/mini-browser`, and writes
the desktop file and scalable icon under the usual XDG data directories.
It also copies the pin extension into the user's GNOME Shell extensions directory;
enable it with the command above after logging out and back in.
Re-running the script updates the installed copy. The launcher uses an absolute
executable path, so `~/.local/bin` need not be on the desktop session's PATH.

To remove the installation, delete the `mini-browser` executable, the
`io.github.stone.MiniBrowser.desktop` entry, the matching app icon, and the
`mini-browser/app` directory. Disable Mini Browser Pin in Extensions and remove
`~/.local/share/gnome-shell/extensions/mini-browser-pin@io.github.stone` as well.
Browser data is stored separately; see the user
guide before removing it.

Reference APIs:

- <https://api.pygobject.gnome.org/Gtk-4.0/>
- <https://api.pygobject.gnome.org/Adw-1/>
- <https://api.pygobject.gnome.org/WebKit-6.0/>
