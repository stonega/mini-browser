# Setup and verification

Use `/usr/bin/python3` with distribution-provided PyGObject, GTK 4.12+,
libadwaita 1.5+, WebKitGTK 6.0 (2.40+), and cryptography 44+ with Argon2id support.
A pip-only virtual environment will
not usually see native GI bindings. No Bun/npm dependencies are needed for this
native desktop app.

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita webkitgtk6.0 python3-cryptography ruff
./scripts/run
```

Development commands:

```sh
./scripts/check
ruff format src tests scripts/install
./scripts/smoke
./scripts/build-rpm
```

`check` runs standard-library unit tests, Ruff lint and format checks, and Python
byte compilation. `smoke` requires a running Wayland or X11 session. It uses a
separate application ID, temporary browser data, and a loopback HTTP server.
Pass an optional PNG filename to capture only the test application window.
The password smoke test also creates a temporary vault, imports a synthetic CSV
through the native dialog, checks failed and successful unlocks, then fills a
local WebKit login form. It checks isolated-world setters, exact-site account
selection, origin/document/form guards, and manual locking. Neither test command
imports private Chrome data or uses the system keyring.

## Fedora system package

```sh
sudo dnf install rpm-build desktop-file-utils python3-cryptography ruff
./scripts/build-rpm
sudo dnf install ./dist/mini-browser-0.2.2-1.fc45.noarch.rpm
/usr/bin/mini-browser
```

The build uses `build/rpm` and writes the binary and source RPMs into `dist/`.
The binary package installs `/usr/bin/mini-browser`, modules under
`/usr/share/mini-browser`, the desktop entry, icon, license, and docs. DNF resolves
the declared GTK, WebKitGTK, PyGObject, and cryptography runtime dependencies.
No personal bookmarks, history, cookies, or vault files are packaged or removed.

If migrating from `scripts/install`, move the old user desktop entry
`~/.local/share/applications/io.github.stone.MiniBrowser.desktop` aside so GNOME
uses the packaged entry. Replace `~/.local/bin/mini-browser` with a symlink to
`/usr/bin/mini-browser` or remove that old wrapper. Keep the `mini-browser` data
directory and `passwords.vault`. Restart Mini Browser to load the installed code.

## Alternative user installation

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
