# Release notes

## 0.4.1 — October 29, 2026

- Hide the Home button on Home, including its History tab; keep it available in opened apps.
- Fix the About dialog's app icon by bundling the SVG with the application and
  adding it to GTK's icon search path, including when running from a source checkout.
- Use the same bundled icon for user installations and Fedora RPM desktop integration.

Close all Mini Browser windows and reopen the app after upgrading. The library,
website sessions and encrypted vault are preserved.

## 0.4.0 — October 28, 2026

- Hide the page layout button in Home windows, including the History tab.
- Make Home close the app window and focus an existing Home window, creating a clean one when needed; add Home to phone windows.
- Simplify script-app loading to a centered monospace log with typing animation and a blinking cursor.
- Combine devices, script apps, and bookmarked apps into one Home Apps grid without group titles.
- Show a selectable, monospace agent prompt with a Copy prompt button at the bottom of Home's Apps tab.
- Add persistent Home app tiles through `agent add-app URL --title NAME`, with
  `apps` and `remove-app` commands and matching native MCP tools.
- Add opt-in `--agent` control, a JSON CLI and a native stdio MCP server for Codex.
- Support Chromium snapshots, element input, screenshots, JavaScript and raw CDP,
  plus native GTK inspection, button/text input, window actions and resizing.
- Add the [agent command reference and Codex setup](agents.md) and an isolated
  `./scripts/smoke --test=agent` integration test using synthetic pages and profiles.

Close all Mini Browser windows and reopen the app after upgrading. The library,
website sessions and encrypted vault are preserved. Agent control is opt-in:
start the app with `mini-browser --agent`; see the [agent guide](agents.md) for
CLI commands and native Codex MCP setup. H.264/AAC remains limited to Fedora 45
and Rawhide x86_64; Fedora 43/44 and aarch64 retain the stock engine.

## 0.3.1 — October 27, 2026

- Enable H.264/AAC playback, including MediaSource streaming, in Fedora 45 and
  Rawhide x86_64 RPMs using a checksum-pinned codec-enabled CEF runtime.
- Retain the stock engine on Fedora 43/44 and aarch64; H.264/AAC remains
  unavailable there. The codec runtime requires glibc 2.44. Widevine is not included.
- Make website Copy buttons write to the GNOME clipboard after a user action,
  including the local DeepSeek page, with origin/document/focus safeguards.
- Remember Full page window sizes separately for websites, phones and script apps.
- Add a DeepSeek Harness Home tile with a managed local server, shared sessions,
  cancellation, crash handling and cleanup when its last window closes.
- Fingerprint the complete installed runtime so changing codecs or resources
  selects a new immutable runtime without overwriting libraries used by open windows.

Close all Mini Browser windows and reopen the app after upgrading. The library,
website sessions and encrypted vault are preserved. The DeepSeek tile requires
Node.js and npm's `npx`; its first launch downloads the upstream CLI.
Synthetic H.264/AAC file and streaming playback passed in the native GTK app.
Anonymous YouTube verification required sign-in, so actual YouTube playback was
not verified end to end.

## 0.3.0 — October 27, 2026

- Migrate the main app to sandboxed Chromium/CEF, retaining GTK and libadwaita.
- Keep native navigation, downloads, password filling, and audio/video layouts.
- Add Refresh Page and Reload Without Cache to the three-line menu.
- Keep Home available in the header and distinguish pinned and unpinned windows.
- Bundle the pinned native runtime in architecture-specific RPMs; include both
  x86_64 and aarch64 SDKs in source RPMs for offline Fedora builds.
- Preserve the library and encrypted vault. Website sign-ins need to be repeated;
  earlier WebKit data is retained. Chromium runtimes now ship with each build.

## 0.2.5 — October 25, 2026

- Document installation and automatic package updates through the Mini Browser
  COPR repository for Fedora 43–45 and Rawhide on x86_64 and aarch64.
- Add maintainer instructions for publishing verified source RPMs to COPR and
  checking every enabled build target.

This release refreshes the packaged documentation. Browser features are unchanged
from 0.2.4. Restart existing browser windows after upgrading.

## 0.2.4 — October 24, 2026

- Group phone tiles under Devices and merge USB/Wi-Fi connections to the same
  physical Android phone, preferring an authorized USB connection.
- Bundle the pinned scrcpy Android server and have the RPM install ADB, removing
  the separate scrcpy installation requirement.
- Detect connected Android phones in the background and show them on Home by
  default, including authorization/offline state. Save a phone to retain its tile
  after disconnecting; otherwise detected tiles disappear when unplugged.
- Save Android phones and iPhones in Settings → Devices, then open their app
  tiles on Home in native phone windows.
- Android streams through the bundled scrcpy server and supports
  basic taps, swipes, navigation keys, and ASCII typing through ADB.
- iPhone viewing uses an authenticated UxPlay AirPlay receiver and phone-side
  Screen Mirroring. Desktop iPhone input and phone audio are not yet supported.
- Phone sessions clear their screens and stop helpers on close or desktop lock.
  Synthetic helper tests cover native H.264/RTP rendering, input, and cleanup.
- Pin browser and phone windows above other windows and across workspaces through
  the bundled Mini Browser Pin GNOME Shell extension. Media layouts enable pinning
  by default and restore the previous pin setting when returning to the page.
- Long-press Back to choose a page from the current window's history. Back returns
  to the full page from media layouts, or to Home when no earlier page exists.
- Improve media layouts with dark audio styling, draggable player surfaces,
  edge-to-edge square artwork, and video sizing that stays stable as sites resize.
  The compact audio window now opens at 420 × 200 logical pixels.

The Fedora RPM requires ADB (`android-tools`) and includes the Android helper.
iPhone viewing requires the optional UxPlay 1.73+ package and local-network AirPlay
access. After first installing the bundled pin extension, log out and back in and
enable it with `gnome-extensions enable mini-browser-pin@io.github.stone`.
See the [setup guide](../implementation/setup.md) for dependencies.

## 0.2.3 — October 24, 2026

- Links requesting another tab or window open in the current window. Use Ctrl+N
  to open a separate window explicitly.
- A Back button on the left of the title bar follows the current page's history.
- Compact audio and video layouts retain the same page and its playback. Choose
  a layout from the menu beside Close and return with Full page or Escape.
- Audio and video title bars hide for an immersive view. Move to the top edge or
  press F10 to reveal them; menus and keyboard focus keep the controls available.
- Video windows initially match the video's intrinsic aspect ratio, including
  portrait video, and fit within the monitor. They remain freely resizable.
- Media layouts restore the previous browsing size and maximized/fullscreen
  state. Closing a player stops its page's playback.

When video metadata is unavailable, video mode uses a 16:9 default. Embedded
frame and shadow-DOM videos are not supported by video mode. Scripted popup
flows requiring a separate window handle or a blank popup are unsupported.
See the [user guide](usage.md) for media and import limitations.

The Fedora RPM installs the launcher in `/usr/bin` and application modules under
`/usr/share/mini-browser`. Updating preserves existing browser data and the
encrypted password vault.
