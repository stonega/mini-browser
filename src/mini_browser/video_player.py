"""Focus an existing video in WebKit's top layer without moving its DOM node."""

import json

from .gtk import GLib, WebKit

WORLD = "mini-browser-video"
INITIALIZE = "globalThis.__miniBrowserVideo = null;"
CONTROL = """
let saved = globalThis.__miniBrowserVideo;
const restore = () => {
    if (!saved) return;
    const video = saved.video;
    if (video.matches(':popover-open')) video.hidePopover();
    video.removeAttribute('popover');
    video.controls = saved.controls;
    if (video.style.cssText === saved.appliedStyle) {
        if (saved.originalStyle === null) video.removeAttribute('style');
        else video.setAttribute('style', saved.originalStyle);
    } else {
        for (const [name, value, priority] of saved.styles) {
            if (value) video.style.setProperty(name, value, priority);
            else video.style.removeProperty(name);
        }
    }
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
const styles = {
    position: 'fixed', top: '0', right: '0', bottom: '0', left: '0',
    width: '100vw', height: '100vh',
    'min-width': '0', 'min-height': '0', 'max-width': 'none', 'max-height': 'none',
    'margin-top': '0', 'margin-right': '0', 'margin-bottom': '0', 'margin-left': '0',
    'padding-top': '0', 'padding-right': '0', 'padding-bottom': '0', 'padding-left': '0',
    'border-top-width': '0', 'border-right-width': '0',
    'border-bottom-width': '0', 'border-left-width': '0',
    'background-color': 'black', 'background-image': 'none',
    'object-fit': 'contain', 'box-sizing': 'border-box', 'aspect-ratio': 'auto',
    display: 'block', visibility: 'visible', opacity: '1', transform: 'none',
    'transition-property': 'none', 'animation-name': 'none', 'pointer-events': 'auto',
    'clip-path': 'none', filter: 'none'
};
saved = {video, controls: video.controls, originalStyle: video.getAttribute('style'),
    styles: Object.keys(styles).map(name =>
    [name, video.style.getPropertyValue(name), video.style.getPropertyPriority(name)])};
globalThis.__miniBrowserVideo = saved;
try {
    video.controls = true;
    video.setAttribute('popover', 'manual');
    for (const [name, value] of Object.entries(styles))
        video.style.setProperty(name, value, 'important');
    saved.appliedStyle = video.style.cssText;
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
