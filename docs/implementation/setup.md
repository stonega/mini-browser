# Setup and verification

Use `/usr/bin/python3` with distribution-provided PyGObject, GTK 4.12+,
libadwaita 1.5+, the bundled CEF 156 runtime, and cryptography 44+ with Argon2id support.
A pip-only virtual environment will
not usually see native GI bindings. No JavaScript build pipeline is needed for
this native desktop app. The optional DeepSeek Harness tile runs the upstream
CLI using Node.js and npm's `npx` from the desktop session's PATH. The first
launch downloads `@deepseek-ai/dsh`; it is not bundled in the RPM. Mini Browser
uses `--no-open`, an explicit loopback bind and an OS-assigned port, then opens
the CLI's authenticated readiness URL. See the
[official CLI reference](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md).

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita python3-cryptography ruff cmake ninja-build gcc-c++ \
  gtk3 nss alsa-lib mesa-libgbm cups-libs systemd-libs
./scripts/build-cef
./scripts/run
```

Development commands:

```sh
./scripts/check
ruff format src tests scripts/install
./scripts/smoke
./scripts/smoke --test=devices  # focused phone UI checks, still in the isolated desktop
./scripts/smoke --test=deepseek # synthetic local-app lifecycle checks; no npm downloads
./scripts/build-rpm
```

The main app uses Chromium through CEF, while its UI remains native GTK.
`./scripts/build-cef` downloads and verifies the pinned official Linux SDK, then
builds the small C++ bridge and subprocess. It supports x86_64 and aarch64; the
latter needs a native ARM build. Allow roughly 2 GiB for SDK and build files.
Use `--sdk /absolute/path` for an extracted SDK, or `--archive path.tar.bz2`
for an offline, checksum-verified build. Runtime output is `build/cef/runtime`.
`MINI_BROWSER_CEF_RUNTIME` can select an alternate compatible runtime.
Linux must permit unprivileged user namespaces. The app keeps Chromium's sandbox
and TLS verification enabled; it has no insecure fallback.

`./scripts/smoke --test=cef` checks the main application's rendered pixels,
renderer sandbox, native input, resize, reload and live video frames. `./scripts/smoke --test=engine` verifies native upload/download, certificate
rejection, both refresh actions, page Copy buttons reaching the native clipboard,
and cookies/storage across a process restart. Clipboard checks use a real pointer
click in the disposable desktop and synthetic text only. The default
smoke suite also tests the Chromium-backed navigation, media and password flows.

### H.264/AAC web video (x86_64)

The standard official CEF SDK does not enable H.264/AAC. Some YouTube videos,
especially livestreams, require these codecs and otherwise report that the
browser cannot play the video. Fedora 45 and Rawhide x86_64 RPMs include the
codec-enabled runtime. A source/user codec build requires x86_64 and glibc 2.44
or newer (Fedora 45+):

```sh
sudo dnf install zstd at-spi2-atk brotli double-conversion fontconfig \
  libxml2 libxslt libwebp opus libdav1d flac-libs \
  freetype harfbuzz highway libXcomposite libXdamage libXrandr pulseaudio-libs
./scripts/build-cef --codecs
./scripts/check
./scripts/smoke --test=codecs
./scripts/install
```

Close all Mini Browser windows and reopen it after installing. This option
replaces `build/cef/runtime` with a checksum-pinned Arch Linux CEF build using
Chromium 156.0.8078.48, the same Chromium revision as the standard runtime.
It builds the bridge against the exact matching official SDK and bundles only
the required private libjpeg, simdutf, minizip and OpenH264 libraries and licenses.
The script checks remaining system dependencies before linking. Downloads are
build-time only; no system package or browser profile is replaced. The source
RPM includes all pinned inputs for offline builds; it selects the stock official
SDK on Fedora 43/44 and aarch64, where the codec runtime's ABI is unavailable.
The ordinary `build-cef` command still selects the stock SDK. The codec option
currently supports x86_64 only and does not add
Widevine/DRM support. Runtime package provenance and SHA-256 pins are recorded in
`native/cef/codecs.json` and copied into the built runtime.

The dedicated codec check requires FFmpeg with the `libopenh264`, `aac`,
`libvpx-vp9` and `libopus` encoders (`ffmpeg-free` plus `openh264` on Fedora).
It serves synthetic files from loopback and verifies decoded colored frames,
playback progress and H.264/AAC MediaSource streaming in the disposable desktop.
Run it explicitly for codec-enabled builds; the standard smoke suite remains
usable with the stock CEF runtime.

## Phone mirroring dependencies

Phone windows additionally use GStreamer's introspection bindings, H.264 parser
and decoder, RTP plugins, and the native GTK 4 paintable sink. On Fedora:

```sh
sudo dnf install gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good \
  gstreamer1-plugins-bad-free gstreamer1-plugin-gtk4 gstreamer1-plugin-openh264
```

Mini Browser bundles the unmodified scrcpy 5.0.1 Android server, its Apache-2.0
license, attribution, and release provenance in `src/mini_browser/vendor/scrcpy`.
Its pinned SHA-256 is verified before every connection. The desktop scrcpy
executable and an external scrcpy package are not required; nothing is downloaded
at runtime. Source and user installations include the same bundle as the RPM.

Android still uses `adb` (Fedora's `android-tools`). The RPM declares it as a
required dependency, so DNF installs it automatically. For source or user-only
installs, make ADB available on PATH (`sudo dnf install android-tools` on Fedora).
USB debugging authorization is required; wireless ADB must already be paired
outside the app. Connected devices are enumerated on a background thread every
three seconds. Authorized, unauthorized, and offline devices appear as Home
tiles automatically; opening a screen always requires user activation.

iPhone viewing requires [UxPlay](https://github.com/FDH2/UxPlay) 1.73 or newer
with `-vrtp` support (`uxplay` is packaged by Fedora). It also needs working
local-network service discovery, usually Avahi for packaged versions, and
firewall access for AirPlay/mDNS. The app does not change firewall rules or
system services. Each receiver uses its own empty configuration, stable local
identity, private client register, and displayed pairing code. Video is forwarded
only over loopback into GTK, without a second display window. Audio is disabled.
Missing dependencies produce a reconnectable error in the phone window.

The phone smoke test requires `openh264enc` and `openh264dec` (provided by
`gstreamer1-plugin-openh264`), along with the plugins above. It creates fake ADB
and UxPlay executables in a temporary directory and puts them ahead of the system
tools; no desktop scrcpy helper is provided. It checks automatic tile discovery,
authorization state, unplugging, saved-device identity, the actual bundled server
transfer, real H.264/RTP rendering, input, reconnect, and session cleanup. Other
UI tests explicitly disable discovery to avoid enumerating real devices.
Optional captures include `-android.png` and `-iphone.png`.

## Verification environment

`check` runs standard-library unit tests, Ruff lint and format checks, and Python
byte compilation. `smoke` starts a disposable GNOME Shell with a 1280 × 1024
virtual monitor, a private Wayland display and D-Bus session, and temporary XDG
directories. It requires `gnome-shell`, `dbus-run-session` (`dbus-daemon` on
Fedora), `at-spi2-core`, and the GStreamer GI typelib. No active graphical session
is needed. The runner starts a separate accessibility bus and registry daemon
before Chromium launches, avoiding private-bus systemd activation failures.
Test windows stay off the user's desktop. Navigation checks cover same-window
target links, script popups, native POST submissions, Back, and explicit new windows.
The runner copies and enables the bundled pin extension only in the disposable
desktop. Media checks verify manual pinning, pinning by default in both player
layouts, and restoration of the page's prior pin setting.
Separate pin checks cover independent windows, rejection of unrelated D-Bus
callers, independent above/workspace state notifications, rapid requests, and
restoration of both original flags on extension shutdown.
Window size checks cover resizing, same-host navigation, distinct hosts, closing
and reopening phone windows, script restarts with new local ports, and preserving
the Full page dimensions when closing a compact player. Unit tests also verify
library upgrades, persistence across library reopen, separate app namespaces,
invalid dimensions, and storage failure handling.
The runner also loads a test-only pointer extension in that disposable desktop.
It only accepts owned MediaSmoke, PinSmoke and EngineSmoke windows, reads/changes independent
compositor flags, injects real pointer drags/clicks, and checks compositor window
positions. Media checks cover draggable audio/video surfaces, clickable playback
controls, the video control strip, normal page
interaction, and Back restoring Full page without navigation. This helper is
never installed with the application.
Audio sinks are disabled only for the
test processes, using a clocked fake GStreamer sink for phone tests and CEF's mute-audio switch for browser tests.
The GNOME process and test process groups are stopped and temporary desktop data
is removed after the run, including failed tests. Each test also uses a separate
application ID, temporary browser data, and a loopback HTTP server.
Pass an optional PNG filename to capture only the test application window.
The media smoke test serves a generated WAV and canvas video. It verifies Chromium
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
local Chromium login form. It checks isolated-world setters, exact-site account
selection, origin/document/form guards, and manual locking. Neither test command
imports private Chrome data or uses the system keyring.

## Fedora system package

Install released packages from
[COPR](https://copr.fedorainfracloud.org/coprs/stonegate/mini-browser/):

```sh
sudo dnf copr enable stonegate/mini-browser
sudo dnf install mini-browser
```

The project targets Fedora 43, 44, 45, and Rawhide on x86_64 and aarch64.
DNF receives future updates after the repository is enabled. Restart Mini Browser
after an upgrade to load the new code.

To build a local package instead:

```sh
sudo dnf install rpm-build desktop-file-utils python3-cryptography ruff cmake ninja-build gcc-c++
./scripts/build-rpm
sudo dnf install ./dist/mini-browser-0.3.1-1.fc45.x86_64.rpm
/usr/bin/mini-browser
```

The source RPM includes pinned official CEF SDKs for both x86_64 and aarch64,
the matching codec SDK and verified codec runtime packages. RPM builds run
offline after source preparation. Fedora 45 and Rawhide x86_64 builds enable
H.264/AAC; Fedora 43/44 and ARM retain the stock engine. Binary RPMs are architecture-specific.
The build uses `build/rpm` and writes the binary and source RPMs into `dist/`.
The binary package installs `/usr/bin/mini-browser`, modules under
`/usr/share/mini-browser`, the desktop entry, icon, license, and docs. DNF resolves
the declared GTK, Chromium, PyGObject, and cryptography runtime dependencies.
No personal bookmarks, history, cookies, or vault files are packaged or removed.

Release maintainers submit the verified source RPM to COPR, then check that each
enabled target succeeds before announcing repository availability:

```sh
./scripts/build-rpm --source-only
copr-cli build --nowait stonegate/mini-browser dist/mini-browser-0.3.1-1.fc45.src.rpm
copr-cli list-builds stonegate/mini-browser
```

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
It copies the built Chromium runtime under `app/cef-runtimes`, selected by the `app/cef` symlink (build it first), including
its license and third-party credits. It also copies the pin extension into the user's GNOME Shell extensions directory;
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
- <https://cef-builds.spotifycdn.com/docs/stable/>

## Agent control checks

`./scripts/smoke --test=agent` exercises the public JSON CLI and native stdio MCP
server against real GTK/CEF on the suite's isolated GNOME desktop. All test pages
and profiles are synthetic. The agent test is included in `./scripts/smoke`.
Deterministic socket, schema, MCP and screenshot-file checks run in
`./scripts/check`. See [agent setup and commands](../user/agents.md).
