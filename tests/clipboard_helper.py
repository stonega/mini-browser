"""A separate desktop app for synthetic cross-process clipboard smoke checks."""

import json
import os
import sys
from pathlib import Path

from mini_browser.gtk import Gdk, Gio, GLib, GObject, Gtk


def main():
    Gtk.init()
    app = Gtk.Application(
        application_id=f"io.github.stone.MiniBrowser.EngineSmoke.Clipboard{os.getpid()}"
    )
    app.register(None)
    clipboard = Gdk.Display.get_default().get_clipboard()
    loop = GLib.MainLoop()
    window = Gtk.ApplicationWindow(
        application=app,
        title="Mini Browser clipboard fixture",
        default_width=250,
        default_height=150,
    )
    window.set_child(Gtk.Entry())
    window.present()

    def copy_key(_controller, value, _code, state):
        if value == Gdk.KEY_c and state & Gdk.ModifierType.CONTROL_MASK:
            texture = Gdk.MemoryTexture.new(
                5, 4, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([7, 180, 61, 255]) * 20), 20
            )
            clipboard.set(GObject.Value(Gdk.Texture, texture))
            Path(sys.argv[2]).touch()
            return True
        return False

    keyboard = Gtk.EventControllerKey()
    keyboard.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keyboard.connect("key-pressed", copy_key)
    window.add_controller(keyboard)

    def copied(connection, result):
        connection.call_finish(result)
        window.set_visible(False)

    def received(clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
            pixbuf = Gdk.pixbuf_get_from_texture(texture)
            print(
                json.dumps(
                    {
                        "width": texture.get_width(),
                        "height": texture.get_height(),
                        "pixel": list(pixbuf.get_pixels()[:4]),
                    }
                ),
                flush=True,
            )
        finally:
            loop.quit()

    def transfer():
        # Wayland exposes the selection only to a client with keyboard focus.
        if not window.is_active():
            return GLib.SOURCE_CONTINUE
        if sys.argv[1] == "supply":
            app.get_dbus_connection().call(
                "io.github.stone.MiniBrowser.TestPointer",
                "/io/github/stone/MiniBrowser/TestPointer",
                "io.github.stone.MiniBrowser.TestPointer",
                "Keys",
                GLib.Variant(
                    "(oau)",
                    (
                        f"{app.get_dbus_object_path()}/window/{window.get_id()}",
                        [Gdk.KEY_Control_L, Gdk.KEY_c],
                    ),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
                copied,
            )
        elif clipboard.get_formats().contain_mime_type("image/png"):
            clipboard.read_texture_async(None, received)
        else:
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(50, transfer)
    loop.run()


if __name__ == "__main__":
    main()
