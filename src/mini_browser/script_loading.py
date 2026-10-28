"""Native loading feedback for a script app before its web server is ready."""

from .gtk import Adw, Gtk


class ScriptLoading(Gtk.Box):
    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=20,
            margin_top=32,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        spinner_type = getattr(Adw, "Spinner", Gtk.Spinner)
        self.spinner = spinner_type(width_request=32, height_request=32, halign=Gtk.Align.CENTER)
        self.append(self.spinner)
        self.append(
            Gtk.Label(
                label="Starting DeepSeek Harness…",
                css_classes=["title-2"],
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
        )
        self.append(
            Gtk.Label(
                label="The first launch may take a few minutes.",
                css_classes=["dim-label"],
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
        )
        self.append(Gtk.Label(label="Startup log", xalign=0, css_classes=["heading"]))
        self.output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
        )
        self.buffer = self.output.get_buffer()
        self.end = self.buffer.create_mark(None, self.buffer.get_end_iter(), False)
        scroll = Gtk.ScrolledWindow(
            child=self.output, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.append(Gtk.Frame(child=scroll, vexpand=True))
        self._text = None

    def start(self):
        self.set_log("")
        self.spinner.set_visible(True)
        if isinstance(self.spinner, Gtk.Spinner):
            self.spinner.start()

    def set_log(self, text):
        text = text or "Waiting for startup output…"
        if text != self._text:
            self._text = text
            self.buffer.set_text(text)
            self.buffer.move_mark(self.end, self.buffer.get_end_iter())
            self.output.scroll_to_mark(self.end, 0.0, False, 0.0, 1.0)

    def stop(self):
        if isinstance(self.spinner, Gtk.Spinner):
            self.spinner.stop()
        self.spinner.set_visible(False)
        self.buffer.set_text("")
        self._text = None
