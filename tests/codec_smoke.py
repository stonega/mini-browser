"""Decode generated VP9/Opus and H.264/AAC video, including fragmented MP4 MSE."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mini_browser.application import BrowserApplication
from mini_browser.gtk import GLib


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def encode_samples(root):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "Codec checks require FFmpeg with libopenh264, AAC, libvpx-vp9 and libopus."
        )
    for filename, codec in (
        (
            "avc.mp4",
            [
                "-c:v",
                "libopenh264",
                "-b:v",
                "180k",
                "-c:a",
                "aac",
                "-movflags",
                "+frag_keyframe+empty_moov+default_base_moof",
            ],
        ),
        (
            "vp9.webm",
            ["-c:v", "libvpx-vp9", "-b:v", "180k", "-deadline", "realtime", "-c:a", "libopus"],
        ),
    ):
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "4",
                *codec,
                "-y",
                str(root / filename),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Could not encode the synthetic media fixture: " + result.stderr)


async_play = """(async () => {
    const video = document.querySelector('video');
    const mime = MIME;
    const url = URL;
    const supported = MediaSource.isTypeSupported(mime);
    const playable = video.canPlayType(mime);
    if (!supported || !playable) return {supported, playable};
    let frames = 0;
    const next = () => { frames++; video.requestVideoFrameCallback(next); };
    video.requestVideoFrameCallback(next);
    if (MSE) {
        const source = new MediaSource();
        video.src = URL_OBJECT.createObjectURL(source);
        await new Promise(resolve => source.addEventListener('sourceopen', resolve, {once:true}));
        const buffer = source.addSourceBuffer(mime);
        const data = await (await fetch(url)).arrayBuffer();
        await new Promise((resolve, reject) => {
            buffer.addEventListener('updateend', resolve, {once:true});
            buffer.addEventListener('error', () => reject(new Error(
                video.error?.message || 'MediaSource rejected the encoded fixture')), {once:true});
            buffer.appendBuffer(data);
        });
        source.endOfStream();
    } else video.src = url;
    await video.play();
    await new Promise(resolve => setTimeout(resolve, 550));
    const canvas = document.createElement('canvas'); canvas.width=320; canvas.height=180;
    const context = canvas.getContext('2d'); context.drawImage(video, 0, 0);
    const pixels = context.getImageData(0, 0, 320, 180).data;
    const colored = pixels.some((value, index) => index % 4 !== 3 && value > 30);
    const result = {supported, playable, frames, time:video.currentTime,
        width:video.videoWidth,height:video.videoHeight,colored,error:video.error?.code};
    video.pause(); return result;
})().catch(error => ({exception: error.name + ': ' + error.message}))"""


def run():
    with tempfile.TemporaryDirectory(prefix="mini-browser-codecs-") as temporary:
        root = Path(temporary)
        encode_samples(root)
        (root / "index.html").write_text(
            "<!doctype html><title>Codec fixture</title><video controls></video>"
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}/"
        app = BrowserApplication(
            data_dir=root / "data",
            cache_dir=root / "cache",
            discover_devices=False,
            allow_autoplay=True,
            silent=True,
            application_id=f"io.github.stone.MiniBrowser.Codecs.Run{os.getpid()}",
        )
        state, errors = {"started": False, "complete": False}, []
        probes = [
            ("vp9.webm", 'video/webm; codecs="vp9,opus"', False),
            ("avc.mp4", 'video/mp4; codecs="avc1.42E01E,mp4a.40.2"', False),
            ("avc.mp4", 'video/mp4; codecs="avc1.42E01E,mp4a.40.2"', True),
        ]

        def next_probe(window):
            if not probes:
                state["complete"] = True
                print(
                    "PASS: real VP9/Opus and H.264/AAC decoding, "
                    "file and MSE playback, live frames",
                    flush=True,
                )
                window.close()
                return
            name, mime, mse = probes.pop(0)
            # Fixture values are generated local paths, never page/credential input.
            script = (
                async_play.replace("MIME", json.dumps(mime))
                .replace("URL_OBJECT", "URL")
                .replace("const url = URL;", "const url = " + json.dumps(origin + name) + ";")
                .replace("MSE", json.dumps(mse))
            )

            def completed(view, result):
                try:
                    value = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                    print(name, "MSE" if mse else "file", value, flush=True)
                    assert value.get("supported") and value.get("playable"), value
                    assert value["frames"] >= 2 and value["time"] > 0.2 and value["colored"], value
                    assert value["width"] == 320 and value["height"] == 180, value
                    next_probe(window)
                except Exception as error:
                    errors.append(str(error))
                    traceback.print_exc()
                    window.close()
                    app.quit()

            window.webview.evaluate_javascript(script, -1, None, None, None, completed)

        def tick():
            if not app.get_windows():
                return GLib.SOURCE_CONTINUE
            window = app.get_windows()[0]
            if not state["started"]:
                state["started"] = True
                window.load_uri(origin)
            elif window.webview.get_title() == "Codec fixture" and not window.webview.is_loading():
                next_probe(window)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def expired():
            if not state["complete"]:
                errors.append("Codec playback timed out")
                for window in app.get_windows():
                    window.close()
                app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, tick)
        GLib.timeout_add_seconds(55, expired)
        try:
            result = app.run([sys.argv[0]])
            if errors or not state["complete"]:
                raise AssertionError(errors or "Codec checks did not finish")
            return result
        finally:
            server.shutdown()


if __name__ == "__main__":
    raise SystemExit(run())
