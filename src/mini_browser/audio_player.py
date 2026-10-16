"""Native controls for the existing page, without reloading its media."""

from urllib.parse import urlsplit

from .gtk import Gio, GLib, Gtk, Pango, WebKit

WORLD = "mini-browser-audio"
INITIALIZE = "globalThis.__miniBrowserAudio = [];"
CONTROL = """
const media = [...document.querySelectorAll('audio, video')];
const playing = media.filter(item => !item.paused && !item.ended);
let targets = (globalThis.__miniBrowserAudio || []).filter(item => media.includes(item));
if (command === 'pause') {
    targets = playing;
    targets.forEach(item => item.pause());
} else if (command === 'play') {
    if (!targets.length) targets = media.slice(0, 1);
    await Promise.all(targets.map(item => item.play()));
}
globalThis.__miniBrowserAudio = targets;
return {available: media.length > 0, playing: media.some(item => !item.paused && !item.ended)};
"""


class AudioPlayer(Gtk.Box):
    def __init__(self, webview, notify):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=20,
            margin_end=20,
            valign=Gtk.Align.CENTER,
        )
        self.webview = webview
        self.notify = notify
        self._request = None
        self._refresh_pending = False
        self._available = False
        self._playing = False
        manager = webview.get_user_content_manager()
        if not getattr(manager, "_mini_audio_world", False):
            manager.add_script(
                WebKit.UserScript.new_for_world(
                    INITIALIZE,
                    WebKit.UserContentInjectedFrames.TOP_FRAME,
                    WebKit.UserScriptInjectionTime.START,
                    WORLD,
                    None,
                    None,
                )
            )
            manager._mini_audio_world = True

        details = Gtk.Box(spacing=12)
        details.append(Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=32))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.source = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"]
        )
        self.status = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        text.append(self.source)
        text.append(self.status)
        details.append(text)
        self.append(details)

        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.play_button = Gtk.Button(css_classes=["circular", "suggested-action"])
        self.play_button.connect("clicked", self._toggle_playback)
        self.mute_button = Gtk.Button(css_classes=["circular"])
        self.mute_button.connect(
            "clicked", lambda *_: webview.set_is_muted(not webview.get_is_muted())
        )
        controls.append(self.play_button)
        controls.append(self.mute_button)
        self.append(controls)
        self._render()

    def reset(self):
        if self._request:
            self._request.cancel()
            self._request = None
        self._available = False
        self._playing = False
        self._refresh_pending = False
        self._render()

    def refresh(self):
        self.source.set_label(urlsplit(self.webview.get_uri() or "").hostname or "Audio player")
        self._render()
        if not self._request:
            self._run("probe")
        else:
            self._refresh_pending = True

    def _render(self):
        muted = self.webview.get_is_muted()
        playing = self.webview.is_playing_audio() or self._playing
        self.status.set_label("Muted" if muted else "Playing" if playing else "Playback stopped")
        self.mute_button.set_icon_name(
            "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        )
        self.mute_button.set_tooltip_text("Unmute" if muted else "Mute")
        self.play_button.set_icon_name(
            "media-playback-pause-symbolic" if self._playing else "media-playback-start-symbolic"
        )
        self.play_button.set_sensitive(self._available and self._request is None)
        self.play_button.set_tooltip_text(
            ("Pause" if self._playing else "Play")
            if self._available
            else "Open the page to pause or resume this audio"
        )

    def _toggle_playback(self, *_):
        self._run("pause" if self._playing else "play")

    def _run(self, command):
        request = Gio.Cancellable()
        self._request = request
        self._render()

        def finished(view, result):
            if self._request is not request:
                return  # Navigation or closure invalidated this document's result.
            self._request = None
            try:
                value = view.call_async_javascript_function_finish(result)
                self._available = value.object_get_property("available").to_boolean()
                self._playing = value.object_get_property("playing").to_boolean()
            except GLib.Error:
                self._available = False
                self._playing = False
                if command != "probe":
                    self.notify("Could not control this audio. Open the page to continue.")
            self._render()
            if self._refresh_pending:
                self._refresh_pending = False
                self.refresh()

        self.webview.call_async_javascript_function(
            CONTROL,
            -1,
            GLib.Variant("a{sv}", {"command": GLib.Variant("s", command)}),
            WORLD,
            None,
            request,
            finished,
        )
