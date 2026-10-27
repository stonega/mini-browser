"""Main-application GTK/CEF integration test, using generated loopback data only."""

import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.gtk import Gdk, GLib, Gtk


class Handler(BaseHTTPRequestHandler):
    pages = 0

    def do_GET(self):
        if self.path == "/":
            type(self).pages += 1
        body = b"""<!doctype html><title>CEF fixture</title>
        <style>body{margin:0;background:#123456}
        button,input{position:absolute;left:20px;width:240px;height:40px}
        button{top:20px}input{top:80px}</style>
        <button onclick="document.title='clicked'">Synthetic button</button>
        <input oninput="document.title='typed:'+this.value">
        """
        if self.path == "/video":
            body = b"""<!doctype html><title>CEF video ready</title>
            <style>body{margin:0;background:#123456}
            button{position:absolute;left:20px;top:20px;width:240px;height:40px}
            video{position:absolute;left:20px;top:80px;width:320px;height:180px}</style>
            <button id=play>Play synthetic video</button><video id=video muted playsinline></video>
            <canvas id=canvas width=320 height=180 hidden></canvas>
            <script>
            const context=canvas.getContext('2d');let frame=0;
            setInterval(()=>{context.fillStyle=frame++%2?'#ff0000':'#00ff00';
                context.fillRect(0,0,320,180)},100);
            video.srcObject=canvas.captureStream(12);
            play.onclick=()=>video.play().then(()=>document.title='CEF live video')
                .catch(()=>document.title='CEF video blocked');
            </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Gesture:
    def get_current_button(self):
        return 1


def sandboxed_renderers(runtime):
    found = []
    worker = bytes(runtime / "cef-worker")
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            raw = (process / "cmdline").read_bytes()
            if worker not in raw or b"--type=renderer" not in raw:
                continue
            assert b"--no-sandbox" not in raw
            status = (process / "status").read_text()
            fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
            secured = fields["Seccomp"].strip() == "2" and fields["NoNewPrivs"].strip() == "1"
            print(
                "Renderer sandbox status:",
                fields["Seccomp"].strip(),
                fields["NoNewPrivs"].strip(),
                flush=True,
            )
            found.append(secured)
        except (OSError, ProcessLookupError):
            continue
    return bool(found) and all(found)


def run():
    from mini_browser.chromium import runtime_path

    runtime = runtime_path()
    if not (runtime / "libmini_cef.so").is_file():
        raise SystemExit("Build Chromium first: ./scripts/build-cef")
    with tempfile.TemporaryDirectory(prefix="mini-browser-cef-smoke-") as temporary:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        root = Path(temporary)
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            discover_devices=False,
            silent=True,
            application_id=f"io.github.stone.MiniBrowser.CefSmoke.Run{os.getpid()}",
        )
        app.connect_after(
            "activate",
            lambda *_: app.get_windows()[0].load_uri(f"http://127.0.0.1:{server.server_port}/"),
        )
        state = {"phase": 0, "deadline": time.monotonic() + 40}
        errors = []

        def click(view, x, y):
            view._press(Gesture(), 1, x, y)
            view._release(Gesture(), 1, x, y)

        def tick():
            try:
                if time.monotonic() > state["deadline"]:
                    raise TimeoutError(f"CEF fixture phase {state['phase']}")
                window = app.get_active_window()
                view = window.webview if window else None
                if not window or not view or not view.texture:
                    return GLib.SOURCE_CONTINUE
                title = window.get_title()
                if state["phase"] == 0 and title == "CEF fixture":
                    downloader = Gdk.TextureDownloader.new(view.texture)
                    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
                    pixels, _stride = downloader.download_bytes()
                    if pixels.get_data()[:4] != bytes((0x12, 0x34, 0x56, 0xFF)):
                        return GLib.SOURCE_CONTINUE
                    assert sandboxed_renderers(runtime)
                    state["paintable"] = Gtk.WidgetPaintable.new(window)
                    click(view, 40, 35)
                    state["phase"] = 1
                elif state["phase"] == 1 and title == "clicked":
                    click(view, 40, 95)
                    # Exercise the GTK input-method commit path, including Unicode.
                    view.im.emit("commit", "gtk")
                    state["phase"] = 2
                elif state["phase"] == 2 and title == "typed:gtk":
                    window.set_default_size(520, 680)
                    state["phase"] = 3
                elif state["phase"] == 3 and view.texture.get_width() == 520:
                    assert view.texture.get_height() == view.get_height()
                    window.lookup_action("reload-bypass-cache").activate(None)
                    state["phase"] = 4
                elif state["phase"] == 4 and title == "CEF fixture" and Handler.pages >= 2:
                    window.load_uri(f"http://127.0.0.1:{server.server_port}/video")
                    state["phase"] = 5
                elif state["phase"] == 5 and title == "CEF video ready":
                    click(view, 40, 35)
                    state["phase"] = 6
                elif state["phase"] in (6, 7) and title == "CEF live video":
                    downloader = Gdk.TextureDownloader.new(view.texture)
                    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
                    pixels, stride = downloader.download_bytes()
                    offset = 90 * stride + 30 * 4
                    red, green, blue, alpha = pixels.get_data()[offset : offset + 4]
                    # Video color conversion can round individual channels.
                    color = (
                        "red"
                        if red > 240 and green < 15 and blue < 15
                        else "green"
                        if green > 240 and red < 15 and blue < 15
                        else None
                    )
                    if color is None or alpha != 255:
                        return GLib.SOURCE_CONTINUE
                    if state["phase"] == 6:
                        state["video_color"] = color
                        state["phase"] = 7
                        return GLib.SOURCE_CONTINUE
                    if color == state["video_color"]:
                        return GLib.SOURCE_CONTINUE
                    if len(sys.argv) > 1:
                        snapshot = Gtk.Snapshot.new()
                        state["paintable"].snapshot(
                            snapshot, window.get_width(), window.get_height()
                        )
                        texture = window.get_renderer().render_texture(snapshot.to_node(), None)
                        assert texture.save_to_png(sys.argv[1])
                    window.close()
                    state["phase"] = 8
                    app.quit()
                    print(
                        "PASS: native GTK/CEF pixels, sandboxed renderer, click, typing, resize, "
                        "cache-bypassing reload, live video frames and clean close",
                        flush=True,
                    )
                    return GLib.SOURCE_REMOVE
            except Exception as exc:
                errors.append(exc)
                traceback.print_exc()
                for window in app.get_windows():
                    window.close()
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        app.connect("activate", lambda *_: GLib.timeout_add(100, tick))
        try:
            result = app.run([sys.argv[0]])
            if not errors:
                assert state["phase"] == 8
            return 1 if errors else result
        finally:
            server.shutdown()


if __name__ == "__main__":
    raise SystemExit(run())
