"""Centered, animated startup output for a script app."""

import time

from .gtk import GLib, Gtk, Pango


class ScriptLoading(Gtk.Box):
    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=24,
            margin_bottom=24,
            margin_start=32,
            margin_end=32,
        )
        self.output = Gtk.Label(
            xalign=0,
            yalign=0,
            halign=Gtk.Align.CENTER,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            css_classes=["monospace"],
        )
        self.scroll = Gtk.ScrolledWindow(
            child=self.output,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=480,
            valign=Gtk.Align.CENTER,
            vexpand=True,
        )
        self.scroll.get_vadjustment().connect("changed", self._scroll_to_end)
        self.append(self.scroll)
        self._animation_source = 0
        self._target = self._text = ""
        self._cursor_visible = True

    def start(self):
        self.stop()
        self._last_frame = self._cursor_time = time.monotonic()
        self._cursor_visible = True
        self._render()
        self._animation_source = GLib.timeout_add(30, self._animate)

    def set_log(self, text):
        if not self._animation_source or text == self._target:
            return
        if not text.startswith(self._text):
            # Bounded snapshots can drop older lines. Keep their current prefix
            # visible and animate the latest line instead of replaying old output.
            head, separator, _tail = text.rpartition("\n")
            self._text = head + separator
        self._target = text

    def _animate(self):
        now = time.monotonic()
        elapsed, self._last_frame = now - self._last_frame, now
        remaining = len(self._target) - len(self._text)
        if remaining:
            # Catch up with bursts without letting a long log delay new output.
            count = max(1, int(elapsed * max(80, remaining / 0.25)))
            self._text = self._target[: len(self._text) + count]
            self._cursor_visible = True
            self._cursor_time = now
        elif now - self._cursor_time >= 0.5:
            self._cursor_visible = not self._cursor_visible
            self._cursor_time = now
        self._render()
        return GLib.SOURCE_CONTINUE

    def _render(self):
        text = self._text + ("▌" if self._cursor_visible else "\u00a0")
        if text != self.output.get_text():
            self.output.set_text(text)

    @staticmethod
    def _scroll_to_end(adjustment):
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))

    def stop(self):
        if self._animation_source:
            GLib.source_remove(self._animation_source)
            self._animation_source = 0
        self._target = self._text = ""
        self.output.set_text("")
