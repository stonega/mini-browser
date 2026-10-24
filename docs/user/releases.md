# Release notes

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
