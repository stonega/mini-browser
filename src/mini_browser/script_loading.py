"""Centered, animated startup output for a script app."""

import time

from .gtk import Gdk, GLib, Gtk, Pango

MAX_LINES = 3
BLUR_STEPS = 4
BLUR_SECONDS = 0.18
FRAME_MS = 30

_STYLES = Gtk.CssProvider()
_STYLES.load_from_string("""
    .script-loading-blur-1 { filter: blur(1px); opacity: 0.92; }
    .script-loading-blur-2 { filter: blur(2px); opacity: 0.82; }
    .script-loading-blur-3 { filter: blur(3px); opacity: 0.68; }
    .script-loading-blur-4 { filter: blur(4px); opacity: 0.52; }
""")
_STYLES_INSTALLED = False


def _install_styles():
    global _STYLES_INSTALLED
    display = Gdk.Display.get_default()
    if _STYLES_INSTALLED or display is None:
        return
    Gtk.StyleContext.add_provider_for_display(
        display, _STYLES, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _STYLES_INSTALLED = True


class ScriptLoading(Gtk.Box):
    """Show the newest startup lines: one label each, so they can blur in.

    Long lines wrap to the window instead of being clipped, and lines older
    than MAX_LINES drop out of view. There is no scrolling surface, so the
    transcript never grows a scrollbar.
    """

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=24,
            margin_bottom=24,
            margin_start=32,
            margin_end=32,
        )
        _install_styles()
        self.lines = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            valign=Gtk.Align.CENTER,
            vexpand=True,
        )
        self.rows = [
            Gtk.Label(
                xalign=0.5,
                justify=Gtk.Justification.CENTER,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                css_classes=["monospace"],
            )
            for _ in range(MAX_LINES)
        ]
        for row in self.rows:
            row.set_visible(False)
            self.lines.append(row)
        # The newest line is always typed into the last row.
        self.output = self.rows[-1]
        self.append(self.lines)
        self._animation_source = 0
        self._target = self._text = ""
        self._cursor_visible = True
        self._line_count = 0
        self._blur_start = None
        self._blur_shown = 0

    def start(self):
        self.stop()
        self._last_frame = self._cursor_time = time.monotonic()
        self._cursor_visible = True
        self._render()
        self._animation_source = GLib.timeout_add(FRAME_MS, self._animate)

    def set_log(self, text):
        if not self._animation_source or text == self._target:
            return
        if not text.startswith(self._text):
            # Bounded snapshots can drop older lines. Keep their current prefix
            # visible and animate the latest line instead of replaying old output.
            head, separator, _tail = text.rpartition("\n")
            self._text = head + separator
        self._target = text

    @property
    def text(self):
        """The visible log lines, including the end cursor."""
        return "\n".join(row.get_text() for row in self.rows if row.get_visible())

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
        self._advance_blur()
        return GLib.SOURCE_CONTINUE

    def _render(self):
        cursor = "▌" if self._cursor_visible else "\u00a0"
        lines = (self._text + cursor).split("\n")
        visible = lines[-MAX_LINES:]
        offset = MAX_LINES - len(visible)
        for index, row in enumerate(self.rows):
            text = visible[index - offset] if index >= offset else ""
            if row.get_text() != text:
                row.set_text(text)
            row.set_visible(bool(text))
        if len(lines) > self._line_count:
            # The newest line starts blurred and sharpens into focus.
            self._blur_start = time.monotonic()
        self._line_count = len(lines)

    def _advance_blur(self):
        radius = 0
        if self._blur_start is not None:
            remaining = 1 - (time.monotonic() - self._blur_start) / BLUR_SECONDS
            if remaining > 0:
                radius = max(1, round(BLUR_STEPS * remaining))
            else:
                self._blur_start = None
        self._show_blur(radius)

    def _show_blur(self, radius):
        if self._blur_shown == radius:
            return
        row = self.output
        for step in range(1, BLUR_STEPS + 1):
            row.remove_css_class(f"script-loading-blur-{step}")
        if radius:
            row.add_css_class(f"script-loading-blur-{radius}")
        self._blur_shown = radius

    def stop(self):
        if self._animation_source:
            GLib.source_remove(self._animation_source)
            self._animation_source = 0
        self._target = self._text = ""
        self._line_count = 0
        self._blur_start = None
        for row in self.rows:
            row.set_text("")
            row.set_visible(False)
        self._show_blur(0)
