# Phone as an app: feasibility research

Researched 2026-10-24. Android mirroring with basic ADB input and iPhone AirPlay
viewing are now implemented. Device tiles, native windows, lifecycle, H.264/RTP
rendering, and input mapping are tested with synthetic helpers. Physical-device
compatibility has not been validated; iPhone Bluetooth control remains deferred.
The sections below preserve the research and distinguish further proposed work.

The implemented Android adapter bundles a checksum-pinned scrcpy 5.0.1 server
in raw H.264 mode, rather than adopting its private control protocol. A separate
desktop scrcpy installation is not required. Connected Android phones now appear
on Home automatically; unsaved detections are transient and never enter history.
Audio forwarding is disabled on both platforms. See the
[architecture](architecture.md#phone-windows), [setup](../implementation/setup.md#phone-mirroring-dependencies),
and [usage guide](../user/usage.md#phone-apps) for current behavior.

## Intended experience

A saved phone appears as an app tile on Mini Browser's home page. Opening the
tile opens or focuses that phone's native window, whose body shows the live phone
screen. The phone continues running its apps. Keep the existing small native
header and default 420 × 780 size, fitting the video without stretching and
handling rotation. Put pairing, renaming, removal, and connection preferences in
Settings → Devices.

The tile is a persistent device entry with a connection state. Opening an iPhone
tile must also support a waiting screen: the user starts Screen Mirroring from
the phone. Desktop tile activation alone does not establish an AirPlay session.
[Apple's AirPlay instructions](https://support.apple.com/en-us/102661) describe
this phone-side selection.

## Feasibility by platform

| Platform | Live screen | Desktop input | Recommended starting point |
| --- | --- | --- | --- |
| Android | Established | Mouse and keyboard | scrcpy transport, USB first |
| iPhone | Established AirPlay receiver route | Separate Bluetooth/AssistiveTouch route to prototype | UxPlay screen reception first |

**Android.** [scrcpy](https://github.com/Genymobile/scrcpy) supports Linux,
USB/TCP-IP, and keyboard/mouse control without root. Mirroring requires Android
5+ and debugging authorization; audio forwarding requires Android 11+. Use an
explicit selected device identity for every session.

Stock scrcpy creates its own display window. An app-owned GTK rendering surface
requires an adapter. Its [developer documentation](https://github.com/Genymobile/scrcpy/blob/master/doc/develop.md)
describes separate video, audio, and input channels, and explicitly says the
internal protocol can change without compatibility. Pin the adapter and device
server to a matching release; estimate this integration separately from launching
the existing executable.

**iPhone screen.** [UxPlay](https://github.com/FDH2/UxPlay) receives AirPlay on
Linux. Start with a shared local network and phone-initiated Screen Mirroring.
Version 1.73+ provides `-vrtp` and `-artp` for forwarding video/audio into another
renderer, offering a path into our own GTK window. Its current 1.74 branch is
experimental; choose and test an appropriate packaged release. Require pairing
authentication: upstream offers `-pin`, with `-reg` to check registered clients.
DRM-protected Apple video cannot be decoded. Connection compatibility remains
dependent on iOS and the receiver version.

**iPhone input.** Apple documents Bluetooth pointer pairing through
[AssistiveTouch](https://support.apple.com/en-us/111775). The upstream
[AirPlayPC project](https://github.com/gbulog/pcairplay#control-the-mirrored-iphone-on-arch)
reports combining UxPlay video with a Bluetooth LE mouse/keyboard implemented
through BlueZ, including GTK 4 and Wayland. It requires a compatible adapter,
phone-side pairing and AssistiveTouch configuration; its phone stays unlocked.
It reports limitations including true multitouch and protected lock-screen entry.
This is promising prior work, not validation on our Fedora/GNOME stack. Prototype
adapter support, input mapping, keyboard layouts, pairing, and reconnection before
committing to desktop control as a supported feature.

Apple's [iPhone Mirroring](https://support.apple.com/en-us/120421) requires a
supported Mac and macOS, and can operate with the iPhone locked. Those documented
requirements make it an experience reference rather than a supported Linux
backend. Our AirPlay/AssistiveTouch proposal has different connection and lock
behavior.

## Fit with this application

The existing `BrowserApplication` owns browser services, while `BrowserWindow`
owns a WebView. `Library.recent_apps()` stores web origins, and
`BrowserWindow.refresh_recents()` assumes every tile contains a website URL.
Device entries therefore need explicit types and separate persistence.

Proposed design, inferred from the current architecture and upstream capabilities:

- Add a device registry and application-owned session manager, with one session
  and window per saved phone. Keep transport code independent of GTK.
- Add a `DeviceWindow` using libadwaita. Route device tile activation to it and
  retain website navigation behavior for website tiles.
- Decode streams with GStreamer and display them using
  [`gtk4paintablesink`](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
  and `Gtk.Picture`. This sink exposes a `Gdk.Paintable`; forwarding media into
  this surface avoids depending on foreign application windows. Confirm the
  required plugin is packaged on the target Fedora release.
- Model connecting, authorization required, waiting for phone, streaming,
  disconnected, and error states. Show input availability separately from video
  connectivity. Bind each stream and input connection to the selected phone.
- Run transport work away from the GTK thread. Closing the device window stops
  its receiver and input session. Clear frames on disconnect and desktop lock;
  require an explicit resume. Keep pairing credentials private, and keep screen
  frames, input, and device identifiers out of logs and browsing history.

## Suggested implementation sequence

1. Prove in-window rendering on an isolated GNOME Wayland desktop using synthetic
   video. Prove the Android adapter and iPhone receiver integration independently.
2. Add device tiles, Settings, session lifecycle, disconnect handling, and
   capability-aware UI. Deliver Android USB mirroring/control and iPhone viewing.
3. Validate iPhone Bluetooth input as an optional capability with clear setup
   instructions; extend Android connection options after USB works reliably.

Use generated device records and fake transports for deterministic tests. Test
the GTK surface and input coordinates with synthetic frames in the existing
isolated smoke desktop. Before claiming phone compatibility, validate consenting
test devices across supported OS versions, rotation, reconnect, lock, and close.
