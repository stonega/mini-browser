"""CEF idle CPU and hidden-view rendering checks on a disposable desktop."""

import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.gtk import GLib


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!doctype html><title>CEF resources</title>
        <style>body{background:#123456}</style><canvas width=320 height=180></canvas>
        <script>
        window.framesPainted=0;
        const context=document.querySelector('canvas').getContext('2d');
        function paint(){
            context.fillStyle=framesPainted++%2?'red':'green';
            context.fillRect(0,0,320,180);
            requestAnimationFrame(paint);
        }
        </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-resources-") as temporary:
        root = Path(temporary)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            discover_devices=False,
            silent=True,
            application_id=f"io.github.stone.MiniBrowser.Resources.Run{os.getpid()}",
        )
        state = {"phase": 0, "deadline": time.monotonic() + 30}
        errors = []

        def tick():
            try:
                now = time.monotonic()
                assert now < state["deadline"], f"Resource fixture phase {state['phase']}"
                window = app.get_windows()[0]
                view = window.webview
                if state["phase"] == 0:
                    if view.get_title() != "CEF resources" or not view.texture:
                        return GLib.SOURCE_CONTINUE
                    state.update(phase=1, start=now, cpu=time.process_time())
                elif state["phase"] == 1 and now - state["start"] >= 2:
                    cpu = (time.process_time() - state["cpu"]) / (now - state["start"])
                    print(f"CEF static host CPU: {cpu * 100:.1f}% of one core", flush=True)
                    assert cpu < 0.4, "Static page is keeping the GTK/CEF event loop busy"
                    view.evaluate_javascript("requestAnimationFrame(paint)")
                    state.update(phase=2, start=now)
                elif state["phase"] == 2 and now - state["start"] >= 1:
                    assert view.texture
                    state["generation"] = view.generation
                    view.set_visible(False)
                    state.update(phase=3, start=now)
                elif state["phase"] == 3 and now - state["start"] >= 1:
                    assert view.texture is None and view.pending_frame is None
                    assert view.frame_source == 0

                    def hidden(_view, result):
                        try:
                            assert not result.error and result.value["hidden"] is True
                            assert result.value["frames"] > 0, "Animation did not start"
                            state.update(
                                phase=5,
                                start=time.monotonic(),
                                cpu=time.process_time(),
                                frames=result.value["frames"],
                            )
                        except Exception as exc:
                            fail(exc)

                    state["phase"] = 4
                    view.evaluate_javascript(
                        "({hidden:document.hidden,frames:framesPainted})", callback=hidden
                    )
                elif state["phase"] == 5 and now - state["start"] >= 2:
                    cpu = (time.process_time() - state["cpu"]) / (now - state["start"])
                    print(f"CEF hidden host CPU: {cpu * 100:.1f}% of one core", flush=True)
                    assert cpu < 0.4, "Hidden view is keeping the GTK/CEF event loop busy"

                    def stopped(_view, result):
                        try:
                            assert not result.error and result.value == state["frames"]
                            view.set_visible(True)
                            window.stack.set_visible_child_name("web")
                            state.update(phase=7, start=time.monotonic())
                        except Exception as exc:
                            fail(exc)

                    state["phase"] = 6
                    view.evaluate_javascript("framesPainted", callback=stopped)
                elif state["phase"] == 7 and view.texture and now - state["start"] >= 1:
                    assert view.generation == state["generation"], "Showing a view reloaded it"
                    state["phase"] = 8
                    window.close()
                    app.quit()
                    print(
                        "PASS: idle CPU, hidden frame release, stopped animation and rendering "
                        "after show",
                        flush=True,
                    )
                    return GLib.SOURCE_REMOVE
            except Exception as exc:
                fail(exc)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def fail(error):
            errors.append(error)
            traceback.print_exception(error)
            for window in app.get_windows():
                window.close()
            app.quit()

        def activated(*_):
            app.get_windows()[0].load_uri(f"http://127.0.0.1:{server.server_port}/")
            GLib.timeout_add(100, tick)

        app.connect_after("activate", activated)
        try:
            result = app.run([sys.argv[0]])
            return 1 if errors or state["phase"] != 8 else result
        finally:
            server.shutdown()


if __name__ == "__main__":
    raise SystemExit(run())
