"""Native controls for the existing page, without reloading its media."""

import base64
import json
from urllib.parse import urlsplit

from .gtk import Gdk, Gio, GLib, Gtk, Pango, Web

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
const metadata = navigator.mediaSession?.metadata;
const artwork = metadata?.artwork || [];
const cover = artwork[artwork.length - 1]?.src
    || playing.find(item => item.poster)?.poster
    || document.querySelector('video[poster]')?.poster
    || document.querySelector('meta[property="og:image"], meta[name="twitter:image"]')?.content
    || '';
let artworkURL = '';
try {
    const url = new URL(cover, document.baseURI);
    if (cover && ['https:', 'http:', 'data:', 'blob:'].includes(url.protocol)) {
        artworkURL = url.href;
    }
} catch (_) {}
return {
    available: media.length > 0,
    playing: media.some(item => !item.paused && !item.ended),
    title: (metadata?.title || document.title || '').slice(0, 512),
    artist: (metadata?.artist || metadata?.album || '').slice(0, 512),
    artwork: artworkURL.length <= 6 * 1024 * 1024 ? artworkURL : ''
};
"""
ARTWORK = """
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 5000);
try {
    // Fetch inside Chromium, with the page's CORS, CSP and session protections.
    const response = await fetch(url, {signal: controller.signal});
    if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/')) return '';
    const limit = 4 * 1024 * 1024;
    if (Number(response.headers.get('Content-Length')) > limit) return '';
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        length += value.length;
        if (length > limit) { await reader.cancel(); return ''; }
        chunks.push(value);
    }
    const blob = new Blob(chunks, {type: response.headers.get('Content-Type')});
    return await new Promise(resolve => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => resolve('');
        reader.readAsDataURL(blob);
    });
} catch (_) {
    return '';
} finally {
    clearTimeout(timeout);
    controller.abort();
}
"""


class CoverLayout(Gtk.LayoutManager):
    """Let the player's height determine the cover's square allocation."""

    def do_get_request_mode(self, _widget):
        return Gtk.SizeRequestMode.WIDTH_FOR_HEIGHT

    def do_measure(self, _widget, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL and for_size >= 0:
            return for_size, for_size, -1, -1
        return 120, 200, -1, -1

    def do_allocate(self, widget, width, height, baseline):
        widget.get_first_child().allocate(width, height, baseline, None)


class AudioPlayer(Gtk.Box):
    def __init__(self, webview, notify):
        super().__init__(css_classes=["osd"])
        self.webview = webview
        self.notify = notify
        self._request = None
        self._artwork_request = None
        self._artwork_url = ""
        self._timer = 0
        self._refresh_pending = False
        self._available = False
        self._playing = False
        manager = webview.get_user_content_manager()
        if not getattr(manager, "_mini_audio_world", False):
            manager.add_script(
                Web.UserScript.new_for_world(
                    INITIALIZE,
                    Web.UserContentInjectedFrames.TOP_FRAME,
                    Web.UserScriptInjectionTime.START,
                    WORLD,
                    None,
                    None,
                )
            )
            manager._mini_audio_world = True

        self.cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.cover.set_alternative_text("Audio cover")
        self.placeholder = Gtk.Image(
            icon_name="audio-x-generic-symbolic", pixel_size=56, css_classes=["dim-label"]
        )
        artwork = Gtk.Overlay(child=self.cover, css_classes=["osd"])
        artwork.add_overlay(self.placeholder)
        self.cover_frame = Gtk.Box(layout_manager=CoverLayout())
        self.cover_frame.append(artwork)
        self.append(self.cover_frame)

        details = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            hexpand=True,
            valign=Gtk.Align.CENTER,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"])
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False)
        self.source = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "caption"]
        )
        self.status = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        text.append(self.title)
        text.append(self.artist)
        text.append(self.source)
        text.append(self.status)
        details.append(text)
        self.append(details)

        controls = Gtk.Box(spacing=12, halign=Gtk.Align.START)
        self.play_button = Gtk.Button(css_classes=["circular", "suggested-action"])
        self.play_button.connect("clicked", self._toggle_playback)
        self.mute_button = Gtk.Button(css_classes=["circular"])
        self.mute_button.connect(
            "clicked", lambda *_: webview.set_is_muted(not webview.get_is_muted())
        )
        controls.append(self.play_button)
        controls.append(self.mute_button)
        details.append(controls)
        self.connect("map", self._mapped)
        self.connect("unmap", self._unmapped)
        self._render()

    def _mapped(self, *_):
        self.refresh()
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(1, self._poll)

    def _unmapped(self, *_):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _poll(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def reset(self):
        if self._request:
            self._request.cancel()
            self._request = None
        self._available = False
        self._playing = False
        self._refresh_pending = False
        self.title.set_label("")
        self.title.set_tooltip_text(None)
        self.artist.set_label("")
        self.artist.set_tooltip_text(None)
        self.artist.set_visible(False)
        self._set_artwork("")
        self._render()

    def refresh(self):
        self.source.set_label(urlsplit(self.webview.get_uri() or "").hostname or "Audio player")
        if not self.title.get_label():
            self.title.set_label(self.webview.get_title() or "Audio player")
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

    def _set_artwork(self, url):
        if url == self._artwork_url:
            return
        self._artwork_url = url
        if self._artwork_request:
            self._artwork_request.cancel()
            self._artwork_request = None
        self.cover.set_paintable(None)
        self.placeholder.set_visible(True)
        if not url:
            return
        request = Gio.Cancellable()
        self._artwork_request = request

        def finished(view, result):
            if self._artwork_request is not request:
                return
            self._artwork_request = None
            try:
                value = view.call_async_javascript_function_finish(result).to_string()
                if not value:
                    return
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(value)))
            except (GLib.Error, ValueError):
                return  # Missing, blocked or invalid artwork leaves the placeholder.
            self.cover.set_paintable(texture)
            self.placeholder.set_visible(False)

        self.webview.call_async_javascript_function(
            ARTWORK,
            -1,
            GLib.Variant("a{sv}", {"url": GLib.Variant("s", url)}),
            WORLD,
            None,
            request,
            finished,
        )

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
                state = json.loads(value.to_json(0))
                self._available = state["available"]
                self._playing = state["playing"]
                self.title.set_label(state["title"] or "Audio player")
                self.title.set_tooltip_text(state["title"] or None)
                self.artist.set_label(state["artist"])
                self.artist.set_tooltip_text(state["artist"] or None)
                self.artist.set_visible(bool(state["artist"]))
                self._set_artwork(state["artwork"])
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
