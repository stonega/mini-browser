# Release notes

## Unreleased

- Save Android phones and iPhones in Settings → Devices, then open their app
  tiles on Home in native phone windows.
- Android streams through the matching installed scrcpy server and supports
  basic taps, swipes, navigation keys, and ASCII typing through ADB.
- iPhone viewing uses an authenticated UxPlay AirPlay receiver and phone-side
  Screen Mirroring. Desktop iPhone input and phone audio are not yet supported.
- Phone sessions clear their screens and stop helpers on close or desktop lock.
  Synthetic helper tests cover native H.264/RTP rendering, input, and cleanup.

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
