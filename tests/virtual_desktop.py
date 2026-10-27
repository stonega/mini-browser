"""Run the UI smoke suite on a disposable headless GNOME Wayland desktop."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DISPLAY_NAME = "mini-browser-test-0"
PIN_EXTENSION = "mini-browser-pin@io.github.stone"
POINTER_EXTENSION = "mini-browser-pointer-tests@io.github.stone"


def enable_extension(shell, uuid):
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and shell.poll() is None:
        try:
            enabled = bus.call_sync(
                "org.gnome.Shell",
                "/org/gnome/Shell",
                "org.gnome.Shell.Extensions",
                "EnableExtension",
                GLib.Variant("(s)", (uuid,)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            ).unpack()[0]
            if enabled:
                info = bus.call_sync(
                    "org.gnome.Shell",
                    "/org/gnome/Shell",
                    "org.gnome.Shell.Extensions",
                    "GetExtensionInfo",
                    GLib.Variant("(s)", (uuid,)),
                    GLib.VariantType.new("(a{sv})"),
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                ).unpack()[0]
                errors = bus.call_sync(
                    "org.gnome.Shell",
                    "/org/gnome/Shell",
                    "org.gnome.Shell.Extensions",
                    "GetExtensionErrors",
                    GLib.Variant("(s)", (uuid,)),
                    GLib.VariantType.new("(as)"),
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                ).unpack()[0]
                if errors:
                    raise RuntimeError(f"Could not enable {uuid}: {'; '.join(errors)}")
                if info.get("state") == 1:
                    return
        except GLib.Error:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Could not enable {uuid} in virtual GNOME")


def stop_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def accessibility_address():
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.monotonic() + 10
    while True:
        try:
            return bus.call_sync(
                "org.a11y.Bus",
                "/org/a11y/bus",
                "org.a11y.Bus",
                "GetAddress",
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            ).unpack()[0]
        except GLib.Error as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Could not start virtual accessibility: {error}") from error
            time.sleep(0.1)


def silent_audio_environment(address):
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    ranks = []
    for factory in Gst.Registry.get().get_feature_list(Gst.ElementFactory):
        kinds = (factory.get_metadata("klass") or "").split("/")
        if "Sink" in kinds and "Audio" in kinds:
            ranks.append(f"{factory.get_name()}:NONE")
    # autoaudiosink falls back to a clocked fakesink, retaining real playback
    # timing and Chromium audio notifications without opening hardware outputs.
    return {
        **os.environ,
        "GST_PLUGIN_FEATURE_RANK": ",".join(ranks),
        "AT_SPI_BUS_ADDRESS": address,
    }


def run_tests(root, arguments):
    tests = {
        "devices": "device_smoke.py",
        "deepseek": "deepseek_smoke.py",
        "browser": "smoke.py",
        "pin": "pin_smoke.py",
        "media": "media_smoke.py",
        "passwords": "password_smoke.py",
    }
    tests["cef"] = "cef_smoke.py"
    tests["engine"] = "chromium_features_smoke.py"
    filenames = tuple(tests.values())
    if arguments and arguments[0].startswith("--test="):
        selected = arguments[0].split("=", 1)[1]
        if selected not in tests:
            raise ValueError(
                "Choose --test=devices, deepseek, browser, pin, media, passwords, cef, or engine."
            )
        filenames, arguments = (tests[selected],), arguments[1:]
    log_path = root / "gnome-shell.log"
    command = [
        "gnome-shell",
        "--headless",
        "--wayland",
        "--no-x11",
        "--wayland-display",
        DISPLAY_NAME,
        "--virtual-monitor",
        "1280x1024",
    ]
    with log_path.open("wb") as log:
        launcher = next(
            (
                path
                for path in (
                    "/usr/libexec/at-spi-bus-launcher",
                    "/usr/lib/at-spi2-core/at-spi-bus-launcher",
                )
                if Path(path).is_file()
            ),
            None,
        )
        if launcher is None:
            raise RuntimeError("UI tests require the at-spi2-core accessibility bus launcher")
        # Start this directly: Fedora's session service activation can be denied
        # outside the normal systemd-managed desktop session.
        accessibility = subprocess.Popen(
            [launcher, "--launch-immediately"], stdout=log, stderr=log, start_new_session=True
        )
        shell = None
        registry = None
        try:
            address = accessibility_address()
            registry_path = next(
                (
                    path
                    for path in (
                        "/usr/libexec/at-spi2-registryd",
                        "/usr/lib/at-spi2-core/at-spi2-registryd",
                    )
                    if Path(path).is_file()
                ),
                None,
            )
            if registry_path is None:
                raise RuntimeError("UI tests require the at-spi2-core registry daemon")
            # The private accessibility bus cannot activate a user systemd unit.
            registry = subprocess.Popen(
                [registry_path],
                env={**os.environ, "AT_SPI_BUS_ADDRESS": address},
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            shell = subprocess.Popen(
                command,
                env={**os.environ, "AT_SPI_BUS_ADDRESS": address},
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            socket = root / "runtime" / DISPLAY_NAME
            deadline = time.monotonic() + 25
            while not socket.is_socket():
                if shell.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Virtual GNOME did not start a Wayland display")
                time.sleep(0.1)
            environment = silent_audio_environment(address)
            enable_extension(shell, PIN_EXTENSION)
            enable_extension(shell, POINTER_EXTENSION)
            print("Running UI tests in virtual GNOME (1280 × 1024, silent audio)", flush=True)
            for filename in filenames:
                if shell.poll() is not None:
                    raise RuntimeError("Virtual GNOME exited during the UI tests")
                command = [sys.executable, str(PROJECT / "tests" / filename), *arguments]
                test = subprocess.Popen(
                    command,
                    env=environment,
                    start_new_session=True,
                )
                try:
                    result = test.wait(timeout=90)
                finally:
                    stop_process_group(test)
                if result:
                    print(log_path.read_text(errors="replace")[-6000:], file=sys.stderr)
                    print(
                        f"{filename} exited with status {result} (PID {test.pid})", file=sys.stderr
                    )
                    return result if result > 0 else 1
            return 0
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            print(str(error), file=sys.stderr)
            print(log_path.read_text(errors="replace")[-6000:], file=sys.stderr)
            return 1
        finally:
            if shell:
                stop_process_group(shell)
            if registry:
                stop_process_group(registry)
            stop_process_group(accessibility)


def isolated_environment(root):
    environment = os.environ.copy()
    for name in (
        "DISPLAY",
        "XAUTHORITY",
        "SESSION_MANAGER",
        "DBUS_SESSION_BUS_ADDRESS",
        "DBUS_STARTER_ADDRESS",
        "DBUS_STARTER_BUS_TYPE",
        "AT_SPI_BUS_ADDRESS",
        "XDG_SESSION_ID",
        "PIPEWIRE_REMOTE",
        "PULSE_SERVER",
        "WEBKIT_A11Y_BUS_ADDRESS",
    ):
        environment.pop(name, None)
    for name, directory in (
        ("XDG_RUNTIME_DIR", "runtime"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        path = root / directory
        path.mkdir(mode=0o700)
        environment[name] = str(path)
    environment.update(
        WAYLAND_DISPLAY=DISPLAY_NAME,
        GDK_BACKEND="wayland",
        GSETTINGS_BACKEND="memory",
        GIO_USE_VFS="local",
        GVFS_DISABLE_FUSE="1",
        XDG_SESSION_TYPE="wayland",
        XDG_CURRENT_DESKTOP="GNOME",
        GNOME_SHELL_SESSION_MODE="user",
        PYTHONFAULTHANDLER="1",
    )
    return environment


def main():
    if sys.argv[1:2] == ["--session"]:
        return run_tests(Path(sys.argv[2]), sys.argv[3:])
    for executable in ("gnome-shell", "dbus-run-session"):
        if not shutil.which(executable):
            raise SystemExit(f"UI tests require {executable}; install GNOME Shell and dbus-daemon.")
    with tempfile.TemporaryDirectory(prefix="mini-browser-gnome-") as temporary:
        root = Path(temporary)
        environment = isolated_environment(root)
        shutil.copytree(
            PROJECT / "data/gnome-shell" / PIN_EXTENSION,
            root / "data/gnome-shell/extensions" / PIN_EXTENSION,
        )
        shutil.copytree(
            PROJECT / "tests/gnome-shell" / POINTER_EXTENSION,
            root / "data/gnome-shell/extensions" / POINTER_EXTENSION,
        )
        log_path = root / "session.log"
        with log_path.open("wb") as log:
            result = subprocess.call(
                [
                    "dbus-run-session",
                    "--",
                    sys.executable,
                    __file__,
                    "--session",
                    str(root),
                    *sys.argv[1:],
                ],
                env=environment,
                stderr=log,
            )
        if result:
            print(log_path.read_text(errors="replace")[-10000:], file=sys.stderr)
        # D-Bus-activated portal helpers can leave disconnected FUSE mounts
        # after their private bus exits. Detach only this run's known mounts.
        unmount = shutil.which("fusermount3") or shutil.which("fusermount")
        if unmount:
            for name in ("doc", "gvfs"):
                subprocess.run(
                    [unmount, "-uz", str(root / "runtime" / name)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        return result


if __name__ == "__main__":
    raise SystemExit(main())
