# Setup and verification

Use `/usr/bin/python3` with distribution-provided PyGObject, GTK 4.12+,
libadwaita 1.5+, and WebKitGTK 6.0 (2.40+). A pip-only virtual environment will
not usually see native GI bindings. No Bun/npm dependencies are needed for this
native desktop app.

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita webkitgtk6.0 ruff
./scripts/run
```

Development commands:

```sh
./scripts/check
ruff format src tests scripts/install
./scripts/smoke
```

`check` runs standard-library unit tests, Ruff lint and format checks, and Python
byte compilation. `smoke` requires a running Wayland or X11 session. It uses a
separate application ID, temporary browser data, and a loopback HTTP server.
Pass an optional PNG filename to capture only the test application window.
Neither test command imports private Chrome data.

`scripts/install` copies the Python package under
`$XDG_DATA_HOME/mini-browser/app`, adds `~/.local/bin/mini-browser`, and writes
the desktop file and scalable icon under the usual XDG data directories.
Re-running the script updates the installed copy. The launcher uses an absolute
executable path, so `~/.local/bin` need not be on the desktop session's PATH.

To remove the installation, delete the `mini-browser` executable, the
`io.github.stone.MiniBrowser.desktop` entry, the matching app icon, and the
`mini-browser/app` directory. Browser data is stored separately; see the user
guide before removing it.

Reference APIs:

- <https://api.pygobject.gnome.org/Gtk-4.0/>
- <https://api.pygobject.gnome.org/Adw-1/>
- <https://api.pygobject.gnome.org/WebKit-6.0/>
