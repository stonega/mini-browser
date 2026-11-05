"""Discover a loaded document's app icon; Chromium downloads and decodes it."""

import base64
import json
from urllib.parse import urlsplit

from .gtk import Gio, GLib
from .navigation import is_web_url

ICON_LINKS = """
const candidates = [];
const size = value => Math.max(0, ...String(value || '').split(/\\s+/).map(s =>
    s === 'any' ? 512 : Math.min(512, Number(s.split('x')[0]) || 0)));
const add = (value, base, priority, sizes) => {
    try {
        const url = new URL(value, base);
        if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
            candidates.push({url: url.href, priority, size: size(sizes)});
    } catch (_) {}
};
const links = Array.from(document.querySelectorAll('link[rel]')).slice(0, 100);
for (const link of links) {
    const rel = link.rel.toLowerCase().split(/\\s+/);
    if (rel.includes('apple-touch-icon') || rel.includes('apple-touch-icon-precomposed'))
        add(link.href, document.baseURI, 2, link.sizes?.value);
    else if (rel.includes('icon'))
        add(link.href, document.baseURI, 1, link.sizes?.value);
}
const manifest = links.find(link => link.rel.toLowerCase().split(/\\s+/).includes('manifest'));
if (manifest) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
        const response = await fetch(manifest.href, {signal: controller.signal});
        if (response.ok && Number(response.headers.get('Content-Length')) <= 131072) {
            const reader = response.body.getReader();
            const chunks = [];
            let length = 0;
            while (true) {
                const {value, done} = await reader.read();
                if (done) break;
                length += value.length;
                if (length > 131072) throw new Error('Manifest too large');
                chunks.push(value);
            }
            const data = JSON.parse(await new Blob(chunks).text());
            for (const icon of (Array.isArray(data.icons) ? data.icons : []).slice(0, 30))
                if (typeof icon.src === 'string' &&
                    (!icon.purpose || icon.purpose.split(/\\s+/).includes('any')))
                    add(icon.src, response.url, 3, icon.sizes);
        }
    } catch (_) {} finally { clearTimeout(timeout); controller.abort(); }
}
add('/favicon.ico', location.origin, 0, '32x32');
candidates.sort((a, b) => b.priority - a.priority || b.size - a.size);
return {uri: location.href, icons: [...new Set(candidates.map(item => item.url))].slice(0, 8)};
"""


class SiteIconCapture:
    def __init__(self, view, cache, changed):
        self.view, self.cache, self.changed = view, cache, changed
        self.request = None

    def cancel(self):
        if self.request:
            self.request.cancel()
            self.request = None

    def capture(self):
        self.cancel()
        view = self.view
        uri, generation = view.get_uri(), view.generation
        if not is_web_url(uri):
            return
        request = self.request = Gio.Cancellable()

        def current():
            return (
                self.request is request
                and not request.is_cancelled()
                and not view.closed
                and generation == view.generation
                and uri == view.get_uri()
            )

        def download(icons):
            if not current() or not icons:
                return
            url, *remaining = icons
            parts = urlsplit(url)
            if (
                not is_web_url(url)
                or parts.username is not None
                or parts.password is not None
                or (uri.startswith("https:") and parts.scheme != "https")
            ):
                download(remaining)
                return

            def downloaded(data, error):
                if not current():
                    return
                try:
                    if error or not data.get("data"):
                        download(remaining)
                        return
                    png = base64.b64decode(data["data"], validate=True)
                    changed = self.cache.save(uri, png)
                except (ValueError, OSError):
                    download(remaining)
                    return
                if changed:
                    self.changed()

            view.command("MiniBrowser.downloadIcon", {"url": url, "document": uri}, downloaded)

        def discovered(view, result):
            if not current():
                return
            try:
                data = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
                if data.get("uri") == uri:
                    download(data["icons"])
            except (GLib.Error, ValueError, KeyError):
                pass

        view.call_async_javascript_function(
            ICON_LINKS, -1, None, "mini-browser-site-icons", None, request, discovered
        )
