"""Focus an existing video in Chromium's top layer without moving its DOM node."""

import json

from .gtk import GLib, Web

WORLD = "mini-browser-video"
INITIALIZE = "globalThis.__miniBrowserVideo = null;"
CONTROL = """
let saved = globalThis.__miniBrowserVideo;
const restore = () => {
    if (!saved) return;
    const video = saved.video;
    video.controls = saved.controls;
    if (saved.marker === null) video.removeAttribute('data-mini-browser-video');
    else video.setAttribute('data-mini-browser-video', saved.marker);
    globalThis.__miniBrowserVideo = null;
};
const dimensions = async video => {
    if (!video.videoWidth || !video.videoHeight) {
        await new Promise(resolve => {
            const ready = () => {
                clearTimeout(timer); video.removeEventListener('loadedmetadata', ready); resolve();
            };
            const timer = setTimeout(ready, 1500);
            video.addEventListener('loadedmetadata', ready, {once: true});
        });
    }
    return globalThis.__miniBrowserVideo === saved ?
        {width: video.videoWidth, height: video.videoHeight} : false;
};
if (command === 'exit') {
    if (saved && document.fullscreenElement?.contains(saved.video))
        await document.exitFullscreen();
    restore(); return true;
}
if (!document.fullscreenEnabled) return false;
const videos = [...document.querySelectorAll('video')].filter(video => {
    const rect = video.getBoundingClientRect();
    const style = getComputedStyle(video);
    return rect.width > 0 && rect.height > 0 && !video.hasAttribute('data-mini-browser-video') &&
        style.visibility === 'visible' && style.opacity !== '0';
});
if (command === 'probe') return !!(saved?.video.isConnected || videos.length);
if (saved?.video.isConnected) return await dimensions(saved.video);
const area = video => {
    const rect = video.getBoundingClientRect(); return rect.width * rect.height;
};
videos.sort((a, b) => area(b) - area(a));
const video = videos.find(item => !item.paused && !item.ended) || videos[0];
if (!video) return false;
if (document.fullscreenElement) await document.exitFullscreen();
saved = {video, controls: video.controls, marker: video.getAttribute('data-mini-browser-video')};
globalThis.__miniBrowserVideo = saved;
try {
    video.controls = true;
    video.setAttribute('data-mini-browser-video', 'active');
    await video.requestFullscreen({navigationUI: 'hide'});
    if (document.fullscreenElement !== video) { restore(); return false; }
    return await dimensions(video);
} catch (error) { restore(); return false; }
"""


class VideoPlayer:
    def __init__(self, webview):
        self.webview = webview
        self._probe_generation = 0
        manager = webview.get_user_content_manager()
        if not getattr(manager, "_mini_video_world", False):
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
            manager._mini_video_world = True

    def reset(self):
        self._probe_generation += 1

    def probe(self, completed):
        self._probe_generation += 1
        generation = self._probe_generation

        def probed(available):
            if generation == self._probe_generation:
                completed(available)

        self._run("probe", probed)

    def enter(self, completed):
        self._run("enter", completed)

    def leave(self):
        self._run("exit")

    def _run(self, command, completed=None):
        def finished(view, result):
            try:
                success = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
            except GLib.Error:
                success = False
            if completed:
                completed(success)

        self.webview.call_async_javascript_function(
            CONTROL,
            -1,
            GLib.Variant("a{sv}", {"command": GLib.Variant("s", command)}),
            WORLD,
            None,
            None,
            finished,
        )
