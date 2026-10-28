"""Real Chromium media layouts, using a generated loopback WAV and canvas stream."""

import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pin_smoke import window_flags
from smoke import widgets

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gio, GLib, Gtk
from mini_browser.media import video_window_size
from mini_browser.window import (
    AUDIO_HEIGHT,
    AUDIO_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
)
from mini_browser.window_size import web_app_key


def tone():
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(
            b"".join(
                struct.pack("<h", int(100 * math.sin(2 * math.pi * 440 * index / 8000)))
                for index in range(8000 * 30)
            )
        )
    return data.getvalue()


class Handler(BaseHTTPRequestHandler):
    audio = tone()
    cover_requested = threading.Event()
    release_cover = threading.Event()

    def do_GET(self):
        if self.path == "/tone.wav":
            data, content_type = self.audio, "audio/wav"
        elif self.path in ("/cover.svg", "/next-cover.svg", "/slow-cover.svg"):
            color = b"#613583" if self.path == "/cover.svg" else b"#1c71d8"
            data = (
                b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="240">'
                b'<path fill="' + color + b'" d="M0 0h480v240H0z"/>'
                b'<circle cx="240" cy="120" r="86" fill="#c061cb"/>'
                b'<circle cx="240" cy="120" r="32" fill="#241f31"/>'
                b'<circle cx="240" cy="120" r="8" fill="#f6f5f4"/></svg>'
            )
            content_type = "image/svg+xml"
        elif self.path == "/silent":
            data, content_type = b"<title>Silent page</title>", "text/html"
        elif self.path.startswith("/history-"):
            data = {
                "/history-one": b"<title>History one</title>",
                "/history-two": b"<title>&lt;b&gt;History two&lt;/b&gt;</title>",
                "/history-three": b"<title>History three</title>",
            }[self.path]
            content_type = "text/html"
        elif self.path in ("/video", "/portrait-video", "/metadata-video"):
            data = b"""<!doctype html><meta name="viewport" content="width=device-width">
            <title>Local video fixture</title><h1>Page outside the video</h1>
            <style>#video-parent{transform:scale(.8);overflow:hidden;width:240px;height:160px}
            video{width:200px;height:112px}</style>
            <div id="video-parent">
            <video muted style="border:3px solid red;object-fit:cover;
            margin-left:9px;padding-right:7px!important"></video></div>
            <audio src="/tone.wav" loop></audio><canvas width="320" height="180" hidden></canvas>
            <script>
            const canvas = document.querySelector('canvas'); const ctx = canvas.getContext('2d');
            document.querySelector('video').srcObject = canvas.captureStream(10);
            window.framesPainted = 0;
            setInterval(() => {
                ctx.fillStyle = '#3584e4'; ctx.fillRect(0, 0, 320, 180);
                ctx.fillStyle = '#ffffff'; ctx.font = '24px sans-serif';
                ctx.fillText('Mini Browser video', 45, 90);
                ctx.fillRect((window.framesPainted++ % 28) * 10, 140, 20, 10);
            }, 100);
            </script>"""
            if self.path == "/portrait-video":
                data = data.replace(b'width="320" height="180"', b'width="180" height="320"')
            if self.path == "/metadata-video":
                data = data.replace(
                    b"document.querySelector('video').srcObject = canvas.captureStream(10);",
                    b"window.startVideo = () => { "
                    b"document.querySelector('video').srcObject = canvas.captureStream(10); };",
                )
            content_type = "text/html"
        else:
            data = b"""<!doctype html><meta name="viewport" content="width=device-width">
            <title>Local audio fixture</title><audio src="/tone.wav" controls loop></audio>
            <audio id="unused" src="/tone.wav" preload="auto"></audio>
            <script>navigator.mediaSession.metadata = new MediaMetadata({
                title: 'Synthetic Track', artist: 'Fixture Artist',
                artwork: [{src: '/cover.svg', sizes: '480x240', type: 'image/svg+xml'}]
            });</script>"""
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.path == "/slow-cover.svg":
            self.cover_requested.set()
            self.release_cover.wait(5)
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Navigation may cancel a fixture request before its body arrives.

    def log_message(self, *_):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="mini-media-smoke-") as temp:
        root = Path(temp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        app = BrowserApplication(
            discover_devices=False,
            allow_autoplay=True,
            silent=True,
            data_dir=root / "data",
            cache_dir=root / "cache",
            application_id=f"io.github.stone.MiniBrowser.MediaSmoke.Run{os.getpid()}",
        )
        errors = []
        state = {"step": "startup", "completed": False}

        def fail(exc):
            errors.append(f"{state['step']}: {exc}")
            traceback.print_exception(exc)
            app.quit()

        def exception_hook(_kind, value, _trace):
            fail(value)

        def wait(description, condition):
            state["step"] = description
            yield condition

        def javascript(window, body):
            result = {}

            def finished(view, response):
                try:
                    value = view.call_async_javascript_function_finish(response)
                    result["value"] = json.loads(value.to_json(0))
                except Exception as exc:
                    fail(exc)

            window.webview.call_async_javascript_function(
                body, -1, None, None, None, None, finished
            )
            yield from wait("JavaScript result: " + body[:60], lambda: "value" in result)
            return result["value"]

        def toggle_audio(window):
            window.activate_action(
                "win.page-layout" if window.audio_mode else "win.audio-player", None
            )

        def frame(window):
            return (
                app.get_dbus_connection()
                .call_sync(
                    "io.github.stone.MiniBrowser.TestPointer",
                    "/io/github/stone/MiniBrowser/TestPointer",
                    "io.github.stone.MiniBrowser.TestPointer",
                    "Frame",
                    GLib.Variant("(o)", (window.pin.path,)),
                    GLib.VariantType.new("(iiii)"),
                    Gio.DBusCallFlags.NONE,
                    1500,
                    None,
                )
                .unpack()
            )

        def pointer(window, x, y, dx=0, dy=0):
            result = {}

            def finished(connection, response):
                try:
                    connection.call_finish(response)
                    result["done"] = True
                except Exception as error:
                    fail(error)

            app.get_dbus_connection().call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "Pointer",
                GLib.Variant("(odddd)", (window.pin.path, x, y, dx, dy)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                finished,
            )
            yield from wait("real pointer interaction", lambda: result.get("done", False))

        def hold_back(window):
            valid, bounds = window.back_button.compute_bounds(window)
            assert valid
            result = {}

            def finished(connection, response):
                try:
                    connection.call_finish(response)
                    result["done"] = True
                except Exception as error:
                    fail(error)

            app.get_dbus_connection().call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "Hold",
                GLib.Variant(
                    "(oddu)",
                    (
                        window.pin.path,
                        bounds.get_x() + bounds.get_width() / 2,
                        bounds.get_y() + bounds.get_height() / 2,
                        900,
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                4000,
                None,
                finished,
            )
            yield from wait("real Back long press", lambda: result.get("done", False))

        def scenario(window):
            assert window.get_default_size() == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
            yield from wait(
                "pin extension connected", lambda: window.pin.available and not window.pin.pending
            )
            assert window.pin_button.get_next_sibling() == window.layout_button
            assert not window.pin.pinned and not window.pin_button.get_active()
            window.pin_button.emit("clicked")
            yield from wait("manual page pin", lambda: window.pin.pinned and not window.pin.pending)
            assert window_flags(window) == (True, True)
            window.pin_button.emit("clicked")
            yield from wait(
                "manual page unpin", lambda: not window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (False, False)
            menu = window.layout_button.get_menu_model()
            assert [
                menu.get_item_attribute_value(index, "label", None).get_string()
                for index in range(menu.get_n_items())
            ] == ["Full page", "Audio player", "Video player"]
            assert not window.lookup_action("audio-player").get_enabled()
            window.activate_action("win.audio-player", None)
            assert not window.audio_mode
            window.load_uri(origin)
            yield from wait("audio page load", lambda: not window.webview.is_loading())
            assert not window.lookup_action("audio-player").get_enabled()
            yield from javascript(
                window, "await document.querySelector('audio').play(); return true;"
            )
            yield from wait(
                "audio detected", lambda: window.lookup_action("audio-player").get_enabled()
            )
            window.set_default_size(480, 700)
            yield from wait("custom page size", lambda: window.get_height() == 700)
            original_view = window.webview
            generation = window.document_generation
            toggle_audio(window)
            yield from wait(
                "compact size and controls",
                lambda: (
                    window.get_height() == AUDIO_HEIGHT
                    and window.player.play_button.get_sensitive()
                ),
            )
            assert window.get_width() == AUDIO_WIDTH
            yield from wait(
                "audio pins by default", lambda: window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (True, True)
            assert window.pin_button.get_active()
            window.pin_button.emit("clicked")
            yield from wait(
                "audio can unpin", lambda: not window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (False, False)
            window.pin_button.emit("clicked")
            yield from wait("audio can repin", lambda: window.pin.pinned and not window.pin.pending)
            assert window.stack.get_visible_child_name() == "audio"
            assert not window.webview.get_mapped()
            assert window.webview is original_view
            assert window.document_generation == generation
            assert window.webview.is_playing_audio()
            assert window.toolbar.get_extend_content_to_top_edge()
            window._show_back_history()
            assert window.audio_mode
            assert any(
                isinstance(widget, Gtk.Label) and widget.get_label() == "No previous pages"
                for widget in widgets(window.back_history)
            )
            window._cancel_header_hide()
            window._hide_media_header()
            assert window.toolbar.get_reveal_top_bars()
            window.back_history.popdown()
            yield from wait("closed empty history", lambda: not window.back_history.get_mapped())
            window.player.mute_button.grab_focus()
            window._header_leave()
            yield from wait("hidden audio header", lambda: window.toolbar.get_top_bar_height() == 0)
            yield from wait(
                "audio cover and metadata",
                lambda: window.player.cover.get_paintable() is not None,
            )
            assert window.player.title.get_label() == "Synthetic Track"
            assert window.player.artist.get_label() == "Fixture Artist"
            assert not window.player.placeholder.get_visible()

            def check_cover():
                valid, bounds = window.player.cover.compute_bounds(window.player)
                assert valid
                assert bounds.get_x() == bounds.get_y() == 0
                assert bounds.get_width() == bounds.get_height() == window.get_height()
                assert window.player.cover.get_content_fit() == Gtk.ContentFit.COVER

            check_cover()
            original_frame = frame(window)
            yield from pointer(window, 80, 80, 90, 60)
            moved_frame = frame(window)
            assert moved_frame[:2] != original_frame[:2], (original_frame, moved_frame)
            assert moved_frame[2:] == original_frame[2:]
            valid, bounds = window.player.mute_button.compute_bounds(window)
            assert valid
            muted = window.webview.get_is_muted()
            yield from pointer(
                window,
                bounds.get_x() + bounds.get_width() / 2,
                bounds.get_y() + bounds.get_height() / 2,
            )
            yield from wait(
                "audio button still clicks", lambda: window.webview.get_is_muted() != muted
            )
            assert frame(window) == moved_frame
            window.player.mute_button.emit("clicked")
            assert not window.webview.can_go_back()
            assert window.home_button.get_visible()
            assert window.back_button.get_sensitive()
            audio_uri = window.webview.get_uri()
            window.back_button.emit("clicked")
            yield from wait("audio Back restores full page", lambda: window.get_height() == 700)
            assert not window.audio_mode and window.webview.get_uri() == audio_uri
            assert window.document_generation == generation and window.webview.is_playing_audio()
            toggle_audio(window)
            yield from wait(
                "audio after Back",
                lambda: window.get_height() == AUDIO_HEIGHT and not window._layout_source,
            )
            print(
                "PASS: audio surface drag, real playback button click, Back without navigation",
                flush=True,
            )
            window.set_default_size(420, 240)
            yield from wait(
                "resized square cover",
                lambda: window.player.cover.get_width() == window.get_height() == 240,
            )
            check_cover()
            window.set_default_size(AUDIO_WIDTH, AUDIO_HEIGHT)
            yield from wait("compact cover restored", lambda: window.get_height() == AUDIO_HEIGHT)
            window.activate_action("win.show-header", None)
            assert window.toolbar.get_reveal_top_bars()
            assert window._header_has_focus()
            window.player.mute_button.grab_focus()
            window._header_motion(None, 100, AUDIO_HEIGHT - 1)
            yield from wait(
                "audio header hides again", lambda: not window.toolbar.get_reveal_top_bars()
            )
            for menu_button in (window.layout_button, window.menu_button):
                window._header_motion(None, 100, 2)
                assert window.toolbar.get_reveal_top_bars()
                menu_button.popup()
                window._header_leave()
                # Either open menu must remain reachable even after the hide delay.
                yield from javascript(
                    window, "await new Promise(r => setTimeout(r, 1600)); return true;"
                )
                assert window.toolbar.get_reveal_top_bars()
                menu_button.popdown()
                window.player.mute_button.grab_focus()
                window._header_leave()
                yield from wait(
                    "audio menu dismissed", lambda: window.toolbar.get_top_bar_height() == 0
                )
            assert window.layout_button.get_icon_name() == "audio-x-generic-symbolic"
            assert window.player.play_button.get_tooltip_text() == "Pause"
            # A clock still advances while the WebView is hidden.
            before = yield from javascript(
                window, "return document.querySelector('audio').currentTime;"
            )
            yield from javascript(
                window,
                "await new Promise(resolve => setTimeout(resolve, 250)); "
                "return document.querySelector('audio').currentTime;",
            )
            after = yield from javascript(
                window, "return document.querySelector('audio').currentTime;"
            )
            assert before != after, (before, after)
            if len(sys.argv) > 1:
                paintable = state["paintable"]
                snapshot = Gtk.Snapshot.new()
                paintable.snapshot(snapshot, window.get_width(), window.get_height())
                texture = window.get_renderer().render_texture(snapshot.to_node(), None)
                path = Path(sys.argv[1])
                assert texture.save_to_png(str(path.with_name(path.stem + "-audio.png")))

            # A track change updates artwork without changing the page's audio element.
            first_cover = window.player.cover.get_paintable()
            yield from javascript(
                window,
                "navigator.mediaSession.metadata = new MediaMetadata({title:'Next Track', "
                "artist:'Next Artist', artwork:[{src:'/next-cover.svg'}]}); return true;",
            )
            yield from wait(
                "changed track artwork",
                lambda: (
                    window.player.cover.get_paintable() is not None
                    and window.player.cover.get_paintable() is not first_cover
                ),
            )
            assert window.player.title.get_label() == "Next Track"
            assert window.player.artist.get_label() == "Next Artist"
            yield from javascript(
                window,
                "navigator.mediaSession.metadata = new MediaMetadata({title:'No Cover', "
                "artwork:[{src:'/silent'}]}); return true;",
            )
            yield from wait(
                "unavailable cover placeholder",
                lambda: (
                    window.player.title.get_label() == "No Cover"
                    and window.player._artwork_request is None
                ),
            )
            assert window.player.cover.get_paintable() is None
            assert window.player.placeholder.get_visible()
            assert not window.player.artist.get_visible()
            yield from javascript(
                window,
                "navigator.mediaSession.metadata = null; "
                "const meta = document.createElement('meta'); meta.setAttribute('property', "
                "'og:image'); meta.content = '/cover.svg'; "
                "document.head.append(meta); return true;",
            )
            yield from wait(
                "page artwork fallback", lambda: window.player.cover.get_paintable() is not None
            )
            assert window.player.title.get_label() == "Local audio fixture"

            window.player.mute_button.emit("clicked")
            assert window.webview.get_is_muted()
            assert window.player.mute_button.get_tooltip_text() == "Unmute"
            window.player.mute_button.emit("clicked")
            assert not window.webview.get_is_muted()
            yield from wait(
                "mute probe finished", lambda: window.player.play_button.get_sensitive()
            )
            window.player.play_button.emit("clicked")
            yield from wait(
                "paused",
                lambda: (
                    window.player.play_button.get_sensitive()
                    and window.player.play_button.get_tooltip_text() == "Play"
                    and not window.webview.is_playing_audio()
                ),
            )
            paused = yield from javascript(window, "return document.querySelector('audio').paused;")
            assert paused
            assert window.audio_mode and window.lookup_action("page-layout").get_enabled()
            window.player.play_button.emit("clicked")
            yield from wait(
                "resumed",
                lambda: (
                    window.webview.is_playing_audio() and window.player.play_button.get_sensitive()
                ),
            )
            assert (
                yield from javascript(window, "return document.querySelector('#unused').paused;")
            )
            yield from javascript(window, "document.title = 'Updated audio title'; return true;")
            yield from wait("compact title", lambda: window.get_title() == "Updated audio title")
            toggle_audio(window)
            yield from wait("restored size", lambda: window.get_height() == 700)
            assert window.get_width() == 480
            assert window.stack.get_visible_child_name() == "web"
            assert window.webview.is_playing_audio()
            assert window.document_generation == generation
            assert window.toolbar.get_reveal_top_bars()
            assert not window.toolbar.get_extend_content_to_top_edge()
            yield from wait(
                "page pin restored", lambda: not window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (False, False)

            window.pin.set_pinned(True)
            yield from wait(
                "pinned browsing page", lambda: window.pin.pinned and not window.pin.pending
            )
            toggle_audio(window)
            yield from wait("pinned page enters audio", lambda: window.get_height() == AUDIO_HEIGHT)
            window.pin.set_pinned(False)
            yield from wait(
                "unpin this player", lambda: not window.pin.pinned and not window.pin.pending
            )
            toggle_audio(window)
            yield from wait(
                "previous page stays pinned",
                lambda: window.pin.pinned and not window.pin.pending and window.get_height() == 700,
            )
            assert window_flags(window) == (True, True)
            window.pin.set_pinned(False)
            yield from wait(
                "page unpinned again", lambda: not window.pin.pinned and not window.pin.pending
            )

            window.maximize()
            yield from wait("maximized page", window.is_maximized)
            toggle_audio(window)
            yield from wait(
                "compact from maximized",
                lambda: not window.is_maximized() and window.get_height() == AUDIO_HEIGHT,
            )
            toggle_audio(window)
            yield from wait("restored maximized", window.is_maximized)
            window.unmaximize()
            yield from wait(
                "normal page size", lambda: not window.is_maximized() and window.get_height() == 700
            )
            window.fullscreen()
            yield from wait("fullscreen page", window.is_fullscreen)
            toggle_audio(window)
            yield from wait(
                "compact from fullscreen",
                lambda: not window.is_fullscreen() and window.get_height() == AUDIO_HEIGHT,
            )
            toggle_audio(window)
            yield from wait("restored fullscreen", window.is_fullscreen)
            window.unfullscreen()
            yield from wait(
                "normal after fullscreen",
                lambda: not window.is_fullscreen() and window.get_height() == 700,
            )

            # A pause from the website must leave the restore control available.
            toggle_audio(window)
            yield from javascript(
                window,
                "document.querySelector('audio').pause(); return true;",
            )
            yield from wait("page paused audio", lambda: not window.webview.is_playing_audio())
            assert window.audio_mode and window.lookup_action("page-layout").get_enabled()
            toggle_audio(window)
            assert not window.audio_mode
            assert not window.lookup_action("audio-player").get_enabled()
            yield from wait("page mapped after pausing", window.webview.get_mapped)
            yield from javascript(
                window,
                "const audio = document.querySelector('audio'); audio.loop = true; "
                "audio.playbackRate = 1; await audio.play(); return true;",
            )
            yield from wait("replay", lambda: window.lookup_action("audio-player").get_enabled())
            toggle_audio(window)
            yield from javascript(
                window,
                "navigator.mediaSession.metadata = new MediaMetadata({"
                "artwork:[{src:'/slow-cover.svg'}]}); return true;",
            )
            yield from wait("pending cover before navigation", Handler.cover_requested.is_set)
            assert window.player._artwork_request is not None
            window.load_uri(origin + "/silent")
            yield from wait(
                "navigation restores page",
                lambda: (
                    window.webview.get_title() == "Silent page"
                    and not window.webview.is_loading()
                    and window.get_height() == 700
                ),
            )
            assert not window.audio_mode
            assert window.player.cover.get_paintable() is None
            assert window.player.placeholder.get_visible()
            assert not window.player._timer
            assert window.player._artwork_request is None
            Handler.release_cover.set()
            yield from javascript(
                window, "await new Promise(resolve => setTimeout(resolve, 150)); return true;"
            )
            assert window.player.cover.get_paintable() is None
            assert window.stack.get_visible_child_name() == "web"
            assert not window.lookup_action("audio-player").get_enabled()

            # Exercise the fallback for audio without main-document HTML media.
            yield from javascript(
                window,
                "globalThis.context = new AudioContext(); "
                "const source = context.createOscillator(); "
                "const gain = context.createGain(); gain.gain.value = 0.001; "
                "source.connect(gain).connect(context.destination); source.start(); "
                "await context.resume(); return true;",
            )
            yield from wait(
                "Web Audio detected", lambda: window.lookup_action("audio-player").get_enabled()
            )
            toggle_audio(window)
            yield from wait("fallback probe", lambda: window.player._request is None)
            assert not window.player.play_button.get_sensitive()
            window.player.mute_button.emit("clicked")
            assert window.webview.get_is_muted()
            window.player.mute_button.emit("clicked")
            window.home_button.emit("clicked")
            assert not window.audio_mode
            assert window.stack.get_visible_child_name() == "home"
            assert not window.lookup_action("audio-player").get_enabled()
            yield from javascript(window, "await context.close(); return true;")

            window.load_uri(origin + "/video")
            yield from wait(
                "video detected",
                lambda: (
                    window.webview.get_title() == "Local video fixture"
                    and window.lookup_action("video-player").get_enabled()
                ),
            )
            original_style = yield from javascript(
                window, "return document.querySelector('video').style.cssText;"
            )
            yield from javascript(
                window, "await document.querySelector('video').play(); return true;"
            )
            yield from javascript(
                window, "await document.querySelector('audio').play(); return true;"
            )
            video_generation = window.document_generation
            window.activate_action("win.video-player", None)
            yield from wait(
                "video size", lambda: (window.get_width(), window.get_height()) == (640, 360)
            )
            assert window.video_mode and not window.audio_mode
            yield from wait(
                "video pins by default", lambda: window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (True, True)
            yield from wait("hidden video header", lambda: window.toolbar.get_top_bar_height() == 0)
            original_frame = frame(window)
            yield from pointer(window, 240, 120, 75, 45)
            assert frame(window)[:2] != original_frame[:2]
            yield from javascript(
                window,
                "window.videoClicks = 0; "
                "document.addEventListener('click', () => videoClicks++, true); return true;",
            )
            click_frame = frame(window)
            yield from pointer(window, 240, 120)
            clicks = yield from javascript(window, "return videoClicks;")
            assert clicks == 1, clicks
            assert frame(window) == click_frame
            yield from pointer(window, 240, window.get_height() - 24, 30, 0)
            assert frame(window) == click_frame
            video_uri = window.webview.get_uri()
            assert window.home_button.get_visible()
            assert window.back_button.get_sensitive()
            window.back_button.emit("clicked")
            yield from wait("video Back restores full page", lambda: window.get_height() == 700)
            assert not window.video_mode and window.webview.get_uri() == video_uri
            assert window.document_generation == video_generation
            full_frame = frame(window)
            yield from pointer(window, 240, 320, 30, 20)
            assert frame(window) == full_frame
            window.activate_action("win.video-player", None)
            yield from wait("video after Back", lambda: window.get_height() == 360)
            print(
                "PASS: video drag, click/seek controls, normal page drag, Back without navigation",
                flush=True,
            )
            # The real video click above can pause playback; resume the fixture
            # before checking that its clock advances in the existing scenario.
            yield from javascript(
                window, "await document.querySelector('video').play(); return true;"
            )
            assert not window.is_fullscreen()
            assert window.webview is original_view
            assert window.document_generation == video_generation
            dimensions = yield from javascript(
                window,
                "const video = document.querySelector('video'); "
                "const rect = video.getBoundingClientRect(); "
                "return {width:rect.width,height:rect.height,viewportWidth:innerWidth,"
                "viewportHeight:innerHeight,open:document.fullscreenElement===video,"
                "controls:video.controls,parent:video.parentElement.id};",
            )
            assert dimensions["open"] and dimensions["controls"]
            assert dimensions["parent"] == "video-parent"
            assert dimensions["width"] == dimensions["viewportWidth"]
            assert dimensions["height"] == dimensions["viewportHeight"]
            assert abs(dimensions["viewportWidth"] / dimensions["viewportHeight"] - 16 / 9) < 0.01
            window.activate_action("win.show-header", None)
            yield from wait(
                "video header overlays", lambda: window.toolbar.get_top_bar_height() > 0
            )
            overlaid = yield from javascript(
                window, "return {width:innerWidth,height:innerHeight};"
            )
            assert overlaid == {
                "width": dimensions["viewportWidth"],
                "height": dimensions["viewportHeight"],
            }
            window.webview.grab_focus()
            window._header_leave()
            yield from wait("video header hides", lambda: window.toolbar.get_top_bar_height() == 0)
            assert window.layout_button.get_icon_name() == "video-x-generic-symbolic"
            video_time = yield from javascript(
                window, "return document.querySelector('video').currentTime;"
            )
            later = yield from javascript(
                window,
                "await new Promise(resolve => setTimeout(resolve, 250)); "
                "return document.querySelector('video').currentTime;",
            )
            assert later > video_time, (video_time, later)
            if len(sys.argv) > 1:
                snapshot = Gtk.Snapshot.new()
                state["paintable"].snapshot(snapshot, window.get_width(), window.get_height())
                texture = window.get_renderer().render_texture(snapshot.to_node(), None)
                path = Path(sys.argv[1])
                assert texture.save_to_png(str(path.with_name(path.stem + "-video.png")))
            window.activate_action("win.audio-player", None)
            yield from wait("video to audio", lambda: window.get_height() == AUDIO_HEIGHT)
            assert window.audio_mode and not window.video_mode
            assert not (
                yield from javascript(
                    window, "return document.fullscreenElement===document.querySelector('video');"
                )
            )
            window.activate_action("win.video-player", None)
            yield from wait("audio to video", lambda: window.get_height() == 360)
            assert window.video_mode
            window.activate_action("win.page-layout", None)
            yield from wait("restored video page", lambda: window.get_height() == 700)
            yield from wait(
                "video pin restored", lambda: not window.pin.pinned and not window.pin.pending
            )
            assert window_flags(window) == (False, False)
            restored = yield from javascript(
                window,
                "const video = document.querySelector('video'); return {style:video.style.cssText,"
                "controls:video.controls,popover:video.hasAttribute('popover')};",
            )
            assert restored == {"style": original_style, "controls": False, "popover": False}
            window.activate_action("win.video-player", None)
            yield from wait("video before Escape", lambda: window.get_height() == 360)
            yield from javascript(
                window,
                "document.querySelector('video').style.setProperty('--site-marker', 'kept'); "
                "return true;",
            )
            window.activate_action("win.stop", None)
            yield from wait("Escape restores page", lambda: window.get_height() == 700)
            assert not window.video_mode
            styles = yield from javascript(
                window,
                "const style = document.querySelector('video').style; return {"
                "marker:style.getPropertyValue('--site-marker'),margin:style.marginLeft,"
                "padding:style.paddingRight,priority:style.getPropertyPriority('padding-right'),"
                "fit:style.objectFit};",
            )
            assert styles == {
                "marker": "kept",
                "margin": "9px",
                "padding": "7px",
                "priority": "important",
                "fit": "cover",
            }
            # Simulate a site player rewriting even inline important sizing on resize.
            yield from javascript(
                window,
                "window.siteLayoutUpdates = 0; window.updateVideoLayout = () => {"
                "document.querySelector('video').style.cssText = "
                "'width:288px!important;height:162px!important;left:24px;top:32px;' + "
                "'transform:translateX(8px);object-fit:cover;--site-marker:kept'; "
                "window.siteLayoutUpdates++; }; "
                "addEventListener('resize', updateVideoLayout); updateVideoLayout(); return true;",
            )
            window.activate_action("win.video-player", None)
            yield from wait("responsive video size", lambda: window.get_height() == 360)
            for width, height in ((640, 360), (720, 480)):
                window.set_default_size(width, height)
                yield from wait(
                    "resized responsive video",
                    lambda size=(width, height): (window.get_width(), window.get_height()) == size,
                )
                # GTK allocation precedes CEF's asynchronous renderer resize.
                yield from javascript(
                    window,
                    f"const deadline = performance.now() + 5000; "
                    f"while ((innerWidth !== {width} || innerHeight !== {height}) && "
                    "performance.now() < deadline) await new Promise(requestAnimationFrame); "
                    "return true;",
                )
                layout = yield from javascript(
                    window,
                    "updateVideoLayout(); const video = document.querySelector('video'); "
                    "const rect = video.getBoundingClientRect(); return {"
                    "left:rect.left,top:rect.top,width:rect.width,height:rect.height,"
                    "viewportWidth:innerWidth,viewportHeight:innerHeight,"
                    "backdrop:getComputedStyle(video, '::backdrop').backgroundColor,"
                    "style:video.style.cssText,updates:siteLayoutUpdates};",
                )
                assert layout["left"] == layout["top"] == 0, layout
                assert layout["width"] == layout["viewportWidth"] == width, layout
                assert layout["height"] == layout["viewportHeight"] == height, layout
                assert layout["backdrop"] == "rgb(0, 0, 0)", layout
                assert layout["updates"] > 1
            window.activate_action("win.page-layout", None)
            yield from wait("responsive page restored", lambda: window.get_height() == 700)
            restored = yield from javascript(
                window,
                "const video = document.querySelector('video'); return {style:video.style.cssText,"
                "controls:video.controls,popover:video.hasAttribute('popover'),"
                "marker:video.hasAttribute('data-mini-browser-video')};",
            )
            assert restored == {
                "style": layout["style"],
                "controls": False,
                "popover": False,
                "marker": False,
            }, restored
            window.activate_action("win.video-player", None)
            yield from wait("video before navigation", lambda: window.get_height() == 360)
            window.load_uri(origin + "/silent")
            yield from wait(
                "video navigation",
                lambda: (
                    window.webview.get_title() == "Silent page"
                    and not window.webview.is_loading()
                    and window.get_height() == 700
                ),
            )
            assert not window.video_mode
            assert not window.lookup_action("video-player").get_enabled()

            # These synthetic play() calls do not carry a real user gesture.
            window.load_uri(origin + "/portrait-video")
            yield from wait(
                "portrait video detected",
                lambda: (
                    window.webview.get_uri() == origin + "/portrait-video"
                    and not window.webview.is_loading()
                    and window.lookup_action("video-player").get_enabled()
                ),
            )
            yield from javascript(
                window, "/* portrait */ await document.querySelector('video').play(); return true;"
            )
            geometry = (
                window.get_display().get_monitor_at_surface(window.get_surface()).get_geometry()
            )
            portrait_size = video_window_size(
                180, 320, max_width=int(geometry.width * 0.9), max_height=int(geometry.height * 0.8)
            )
            window.activate_action("win.video-player", None)
            yield from wait(
                "portrait size", lambda: (window.get_width(), window.get_height()) == portrait_size
            )
            assert abs(window.get_width() / window.get_height() - 9 / 16) < 0.01
            window.activate_action("win.page-layout", None)
            yield from wait("portrait restored", lambda: window.get_height() == 700)

            window.load_uri(origin + "/metadata-video")
            yield from wait(
                "metadata fixture", lambda: window.lookup_action("video-player").get_enabled()
            )
            assert (
                yield from javascript(window, "return document.querySelector('video').videoWidth;")
            ) == 0
            window.activate_action("win.video-player", None)
            yield from javascript(
                window, "startVideo(); await document.querySelector('video').play(); return true;"
            )
            yield from wait("late metadata size", lambda: window.get_height() == 360)
            window.activate_action("win.page-layout", None)
            yield from wait("metadata restored", lambda: window.get_height() == 700)

            # Missing metadata falls back safely; a cancelled entry cannot resize the page later.
            window.load_uri(origin + "/metadata-video")
            yield from wait(
                "missing metadata fixture",
                lambda: window.lookup_action("video-player").get_enabled(),
            )
            window.activate_action("win.video-player", None)
            window.activate_action("win.page-layout", None)
            yield from javascript(
                window, "await new Promise(r => setTimeout(r, 1700)); return true;"
            )
            assert not window.video_mode and window.get_height() == 700
            window.activate_action("win.video-player", None)
            yield from wait("missing metadata fallback", lambda: window.get_height() == 360)
            assert window.get_width() == 640
            window.activate_action("win.page-layout", None)
            yield from wait("fallback restored", lambda: window.get_height() == 700)

            # A hold opens real Chromium history and must not also click Back.
            for path in ("/history-one", "/history-two", "/history-three"):
                window.load_uri(origin + path)
                yield from wait(
                    "history fixture load",
                    lambda target=origin + path: (
                        window.webview.get_uri() == target and not window.webview.is_loading()
                    ),
                )
            history_length = window.webview.get_back_forward_list().get_length()
            valid, bounds = window.back_button.compute_bounds(window)
            assert valid
            yield from pointer(
                window,
                bounds.get_x() + bounds.get_width() / 2,
                bounds.get_y() + bounds.get_height() / 2,
            )
            yield from wait(
                "short Back click",
                lambda: (
                    window.webview.get_uri() == origin + "/history-two"
                    and not window.webview.is_loading()
                ),
            )
            assert not window.back_history.get_visible()
            window.activate_action("win.forward", None)
            yield from wait(
                "forward before Back hold",
                lambda: (
                    window.webview.get_uri() == origin + "/history-three"
                    and not window.webview.is_loading()
                ),
            )
            yield from hold_back(window)
            assert window.back_history.get_visible()
            assert window.webview.get_uri() == origin + "/history-three"
            rows = [
                widget
                for widget in widgets(window.back_history)
                if isinstance(widget, Gtk.Button) and widget.get_tooltip_text()
            ]
            assert rows[0].get_tooltip_text().endswith("/history-two")
            assert rows[1].get_tooltip_text().endswith("/history-one")
            labels = [widget for widget in widgets(rows[0]) if isinstance(widget, Gtk.Label)]
            assert labels[0].get_label() == "<b>History two</b>" and not labels[0].get_use_markup()
            if len(sys.argv) > 1:
                shot = Gtk.Snapshot.new()
                paintable = Gtk.WidgetPaintable.new(window.back_history)
                paintable.snapshot(
                    shot, window.back_history.get_width(), window.back_history.get_height()
                )
                texture = window.get_renderer().render_texture(shot.to_node(), None)
                path = Path(sys.argv[1])
                assert texture.save_to_png(str(path.with_name(path.stem + "-back-history.png")))
            rows[1].emit("clicked")
            yield from wait(
                "history jumps directly",
                lambda: (
                    window.webview.get_uri() == origin + "/history-one"
                    and not window.webview.is_loading()
                ),
            )
            history = window.webview.get_back_forward_list()
            assert history.get_length() == history_length
            assert history.get_nth_item(1).get_uri() == origin + "/history-two"
            assert history.get_nth_item(2).get_uri() == origin + "/history-three"
            assert not window.back_history.get_visible()
            rows[0].emit("clicked")
            assert window.webview.get_uri() == origin + "/history-one"
            window._show_back_history()
            assert window.back_history.get_visible()
            print(
                "PASS: real Back long press, ordered literal titles, "
                "direct history jump, stale selection guard",
                flush=True,
            )

            # The application remains open in another window when a player closes.
            window.load_uri(origin)
            yield from wait("closing fixture load", lambda: not window.webview.is_loading())
            assert not window.back_history.get_visible()
            yield from javascript(
                window, "await document.querySelector('audio').play(); return true;"
            )
            yield from wait("closing fixture audio", lambda: window.webview.is_playing_audio())
            window.activate_action("win.audio-player", None)
            normal_size = tuple(window._page_size)
            other = app.new_window()
            window.close()
            assert app.library.window_size(*web_app_key(origin)) == normal_size
            yield from wait("closed player unloads", lambda: not original_view.is_playing_audio())
            yield from wait(
                "CEF page destroyed",
                lambda: original_view.identifier not in app.network_session.views,
            )
            assert original_view.get_is_muted()
            assert app.get_windows() == [other]
            assert other.stack.get_visible_child_name() == "home"

            state["completed"] = True
            print(
                "PASS: audio detection, compact size, hidden playback, mute, pause/resume, "
                "square edge-to-edge artwork, track metadata updates and cover fallback, "
                "title, restore size/state, page pause, navigation, Web Audio fallback, home; "
                "immersive header reveal/menus/keyboard, landscape/portrait/metadata video sizing, "
                "live frames, native controls, unchanged DOM parent, "
                "audio/video switching, page restoration, Escape, closed-player cleanup; "
                "manual pins, default media pins, previous pin restoration, "
                "saved Full page size after closing a player"
            )
            other.close()

        def tick():
            try:
                if "steps" not in state:
                    if not app.get_windows():
                        return GLib.SOURCE_CONTINUE
                    window = app.get_windows()[0]
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    state["steps"] = scenario(window)
                    state["waiting"] = next(state["steps"])
                elif state["waiting"]():
                    state["waiting"] = next(state["steps"])
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception as exc:
                fail(exc)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def timeout():
            errors.append(f"Media smoke timed out: {state['step']}")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, tick)
        timeout_source = GLib.timeout_add_seconds(60, timeout)
        previous_hook = sys.excepthook
        sys.excepthook = exception_hook
        try:
            app.run(["mini-media-smoke"])
        finally:
            sys.excepthook = previous_hook
            Handler.release_cover.set()
            if GLib.MainContext.default().find_source_by_id(timeout_source):
                GLib.source_remove(timeout_source)
            server.shutdown()
        if errors or not state["completed"]:
            raise SystemExit("; ".join(errors) or "Media smoke incomplete")


if __name__ == "__main__":
    run()
