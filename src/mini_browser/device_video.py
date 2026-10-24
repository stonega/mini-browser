"""A native GTK paintable backed by a bounded, in-memory GStreamer pipeline."""

import time

from .devices import DeviceError
from .gtk import GLib, Gtk


class DeviceVideo:
    def __init__(self, picture, kind, frame, error, waiting):
        import gi

        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            raise DeviceError(
                "Install the GStreamer introspection bindings, then reconnect."
            ) from exc
        Gst.init(None)
        self.Gst = Gst
        self.picture = picture
        self.on_frame, self.on_error, self.on_waiting = frame, error, waiting
        self.closed = False
        self.dimensions = (0, 0)
        self.last_frame = 0
        self._pending = False
        self._visible = False
        required = ["h264parse", "decodebin", "videoconvert", "gtk4paintablesink"]
        required += (
            ["appsrc"] if kind == "android" else ["udpsrc", "rtph264depay", "rtpjitterbuffer"]
        )
        if any(not Gst.ElementFactory.find(name) for name in required):
            raise DeviceError(
                "Install GStreamer's GTK 4, H.264, and RTP plugins, then reconnect. "
                "See the setup guide for package names."
            )
        source = (
            "appsrc name=source is-live=true format=time do-timestamp=true "
            'block=true max-bytes=2097152 caps="video/x-h264,stream-format=byte-stream"'
            if kind == "android"
            else "udpsrc name=source address=127.0.0.1 port=0 timeout=5000000000 "
            'caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96" '
            "! rtpjitterbuffer latency=50 drop-on-latency=true ! rtph264depay"
        )
        try:
            self.pipeline = Gst.parse_launch(
                source + " ! h264parse ! decodebin ! videoconvert "
                "! gtk4paintablesink name=display sync=false"
            )
        except GLib.Error as exc:
            raise DeviceError(
                "Could not create the phone's video player. Check GStreamer plugins."
            ) from exc
        self.source = self.pipeline.get_by_name("source")
        sink = self.pipeline.get_by_name("display")
        self.paintable = sink.get_property("paintable")
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_paintable(self.paintable)
        self._pad = sink.get_static_pad("sink")
        self._probe_id = self._pad.add_probe(Gst.PadProbeType.BUFFER, self._frame)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self._bus_handler = self.bus.connect("message", self._message)
        result = self.pipeline.set_state(Gst.State.PAUSED)
        if result == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise DeviceError("Could not start the phone's video player. Check GStreamer plugins.")
        self.port = self.source.get_property("port") if kind == "iphone" else None
        self.pipeline.set_state(Gst.State.PLAYING)

    def feed(self, data):
        if not self.closed:
            self.source.emit("push-buffer", self.Gst.Buffer.new_wrapped(data))

    def _frame(self, pad, _info):
        self.last_frame = time.monotonic()
        caps = pad.get_current_caps()
        if caps:
            structure = caps.get_structure(0)
            self.dimensions = structure.get_value("width"), structure.get_value("height")
        if not self._pending and not self.closed:
            self._pending = True
            GLib.idle_add(self._show_frame)
        return self.Gst.PadProbeReturn.OK

    def _show_frame(self):
        self._pending = False
        if not self.closed:
            self.picture.set_paintable(self.paintable)
            self._visible = True
            self.on_frame(self.dimensions)
        return GLib.SOURCE_REMOVE

    def _message(self, _bus, message):
        if self.closed:
            return
        if message.type == self.Gst.MessageType.ERROR:
            self.on_error("Video decoding stopped. Install an H.264 decoder and reconnect.")
        elif message.type == self.Gst.MessageType.EOS:
            self.on_error("The phone's video stream ended. Reconnect to continue.")
        elif message.type == self.Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure and structure.get_name() == "GstUDPSrcTimeout" and self._visible:
                self.picture.set_paintable(None)
                self._visible = False
                self.on_waiting()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.bus.disconnect(self._bus_handler)
        self.bus.remove_signal_watch()
        self.pipeline.set_state(self.Gst.State.NULL)
        self._pad.remove_probe(self._probe_id)
        self.picture.set_paintable(None)
        self.paintable = None
