"""Focus an existing video in WebKit's top layer without moving its DOM node."""

import json

from .gtk import GLib, WebKit

WORLD = "mini-browser-video"
INITIALIZE = "globalThis.__miniBrowserVideo = null;"
# User-origin !important rules outrank even the inline !important sizes that
# site players rewrite on resize. Leave those inline styles owned by the site.
STYLE = """
video[data-mini-browser-video="active"]:popover-open {
    position: fixed !important;
    inset: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    min-width: 0 !important;
    min-height: 0 !important;
    max-width: none !important;
    max-height: none !important;
    margin: 0 !important;
    padding: 0 !important;
    border-width: 0 !important;
    background: black !important;
    object-fit: contain !important;
    box-sizing: border-box !important;
    aspect-ratio: auto !important;
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    transform: none !important;
    transition-property: none !important;
    animation-name: none !important;
    pointer-events: auto !important;
    clip-path: none !important;
    filter: none !important;
}
video[data-mini-browser-video="active"]:popover-open::backdrop {
    background: black !important;
}
"""
CONTROL = """
let saved = globalThis.__miniBrowserVideo;
const restore = () => {
    if (!saved) return;
    const video = saved.video;
    if (video.matches(':popover-open')) video.hidePopover();
    video.removeAttribute('popover');
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
if (!HTMLElement.prototype.showPopover) return false;
const videos = [...document.querySelectorAll('video')].filter(video => {
    const rect = video.getBoundingClientRect();
    const style = getComputedStyle(video);
    return rect.width > 0 && rect.height > 0 && !video.hasAttribute('popover') &&
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
    video.setAttribute('popover', 'manual');
    video.showPopover();
    if (!video.matches(':popover-open')) { restore(); return false; }
    return await dimensions(video);
} catch (error) { restore(); return false; }
"""


class VideoPlayer:
    def __init__(self, webview):
        self.webview = webview
        self._probe_generation = 0
        manager = webview.get_user_content_manager()
        if not getattr(manager, "_mini_video_world", False):
            manager.add_style_sheet(
                WebKit.UserStyleSheet.new_for_world(
                    STYLE,
                    WebKit.UserContentInjectedFrames.TOP_FRAME,
                    WebKit.UserStyleLevel.USER,
                    WORLD,
                    None,
                    None,
                )
            )
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
