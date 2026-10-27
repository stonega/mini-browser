"""Require one consistent native GTK API and Chromium engine."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401, E402

from . import chromium as Web  # noqa: F401, E402
