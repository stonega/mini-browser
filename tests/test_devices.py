"""Phone registry, input mapping, authentication, and cancellable transport checks."""

import base64
import json
import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_browser import scrcpy_bundle
from mini_browser.device_discovery import AndroidDiscovery, device_tiles
from mini_browser.devices import (
    AndroidConnection,
    AndroidTransport,
    Device,
    DeviceError,
    DeviceStore,
    Transport,
    android_tools,
    discover_android,
    iphone_arguments,
    parse_android_devices,
    phone_coordinates,
)
from mini_browser.storage import Library


def fake_helpers(root):
    binary = root / "bin"
    binary.mkdir()
    for name in ("adb", "uxplay"):
        shutil.copyfile(Path(__file__).with_name("phone_helper.py"), binary / name)
        (binary / name).chmod(0o755)
    return {
        "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
        "MINI_BROWSER_PHONE_FIXTURE": str(root),
    }


class DeviceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Library(Path(self.temp.name) / "data/library.sqlite3")
        self.devices = DeviceStore(self.library)

    def test_persistence_deduplication_and_separate_history(self):
        original = self.devices.save("android", "Phone", "synthetic-phone")
        updated = self.devices.save("android", "Renamed", "synthetic-phone")
        self.assertEqual(original.id, updated.id)
        self.assertEqual(DeviceStore(self.library).all(), [updated])
        self.assertEqual(self.library.recent_apps(), [])
        self.assertEqual(self.library.search("history"), [])

    def test_iphones_get_distinct_receiver_identities_and_can_be_removed(self):
        first = self.devices.save("iphone", "iPhone")
        second = self.devices.save("iphone", "iPhone")
        self.assertNotEqual(first.receiver_name, second.receiver_name)
        self.devices.remove(first.id)
        self.assertEqual(self.devices.all(), [second])

    def test_rename_preserves_input_target_and_cannot_resurrect_deleted_device(self):
        phone = self.devices.save("android", "Phone", "synthetic-phone")
        renamed = self.devices.save("android", "New name", "other-phone", device_id=phone.id)
        self.assertEqual(renamed.address, "synthetic-phone")
        self.devices.remove(phone.id)
        with self.assertRaises(ValueError):
            self.devices.save("android", "New name", "synthetic-phone", device_id=phone.id)

    def test_invalid_names_and_addresses_are_rejected(self):
        for kind, name, address in (
            ("unknown", "Phone", ""),
            ("iphone", "", ""),
            ("iphone", "Phone\nprivate", ""),
            ("android", "Phone", "phone; touch /tmp/no"),
            ("android", "Phone", "-s other"),
        ):
            with self.subTest(kind=kind, name=name), self.assertRaises(ValueError):
                self.devices.save(kind, name, address)
        self.assertEqual(self.devices.all(), [])


class DeviceMappingTests(unittest.TestCase):
    def test_discovery_resolves_hardware_identity_and_manual_list_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = fake_helpers(root)
            (root / "devices.json").write_text(
                json.dumps(
                    [
                        ["192.0.2.10:5555", "Pixel 9", "device"],
                        ["synthetic-phone", "Pixel 9", "device"],
                    ]
                )
            )
            (root / "identities.json").write_text(
                json.dumps(
                    {
                        "192.0.2.10:5555": "synthetic-phone",
                        "synthetic-phone": "synthetic-phone",
                    }
                )
            )
            with patch.dict(os.environ, environment):
                records = discover_android()
            self.assertEqual(records, [("synthetic-phone", "Pixel 9", "device")])

    def test_usb_and_wifi_share_one_tile_and_two_same_model_phones_stay_separate(self):
        wifi = AndroidConnection("192.0.2.10:5555", "Pixel 9", "device", "phone-A")
        usb = AndroidConnection("phone-A", "Pixel 9", "device", "phone-A", True)
        first = device_tiles([], [wifi, usb])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0].address, usb.address)
        wireless_only = device_tiles([], [wifi])
        self.assertEqual(wireless_only[0][0].id, first[0][0].id)
        self.assertEqual(wireless_only[0][0].address, wifi.address)
        other = AndroidConnection("phone-B", "Pixel 9", "device", "phone-B", True)
        self.assertEqual(len(device_tiles([], [wifi, usb, other])), 2)

    def test_existing_saved_aliases_upgrade_without_duplicate_tiles_or_lost_names(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Library(Path(temp) / "library.sqlite3")
            with library.connect() as db:
                db.execute(
                    "CREATE TABLE devices (id TEXT PRIMARY KEY, kind TEXT, name TEXT, address TEXT)"
                )
                db.execute("INSERT INTO devices VALUES ('first', 'android', 'My Pixel', 'phone-A')")
                db.execute(
                    "INSERT INTO devices VALUES "
                    "('second', 'android', 'Pixel WiFi', '192.0.2.10:5555')"
                )
            store = DeviceStore(library)
            wifi = AndroidConnection("192.0.2.10:5555", "Pixel 9", "device", "phone-A")
            usb = AndroidConnection("phone-A", "Pixel 9", "device", "phone-A", True)
            store.bind_android_identities([wifi, usb])
            tiles = device_tiles(store.all(), [wifi, usb])
            self.assertEqual(len(tiles), 1)
            self.assertEqual((tiles[0][0].id, tiles[0][0].name), ("first", "My Pixel"))
            self.assertEqual(len(device_tiles(store.all(), [])), 1)
            self.assertEqual(device_tiles(store.all(), [wifi])[0][0].address, wifi.address)

    def test_saving_both_transports_updates_one_saved_phone(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DeviceStore(Library(Path(temp) / "library.sqlite3"))
            usb = store.save("android", "Pixel", "phone-A", identity="phone-A")
            wifi = store.save("android", "My Pixel", "192.0.2.10:5555", identity="phone-A")
            self.assertEqual(usb.id, wifi.id)
            self.assertEqual(len(store.all()), 1)

    def test_automatic_tiles_do_not_persist_and_keep_identity_when_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DeviceStore(Library(Path(temp) / "library.sqlite3"))
            connected = [("synthetic-phone", "Detected phone", "unauthorized")]
            transient, status = device_tiles([], connected)[0]
            self.assertEqual(status, "Allow USB debugging")
            self.assertEqual(store.all(), [])
            saved = store.save("android", "My phone", "synthetic-phone")
            self.assertEqual(saved.id, transient.id)
            self.assertEqual(device_tiles(store.all(), connected), [(saved, status)])
            self.assertEqual(device_tiles(store.all(), []), [(saved, "Disconnected")])
            self.assertEqual(device_tiles([], []), [])

    def test_background_detection_updates_on_connect_authorization_and_disconnect(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = root / "devices.json"
            events = queue.Queue()
            with patch.dict(os.environ, fake_helpers(root)):
                discovery = AndroidDiscovery(lambda *result: events.put(result), interval=0.05)
                discovery.start()
                try:
                    connected, error = events.get(timeout=5)
                    self.assertIsNone(error)
                    self.assertEqual(connected[0][2], "device")
                    records.write_text(json.dumps([["synthetic-phone", "Phone", "unauthorized"]]))
                    connected, error = events.get(timeout=5)
                    self.assertEqual(connected[0][2], "unauthorized")
                    records.write_text("[]")
                    self.assertEqual(events.get(timeout=5), ((), None))
                finally:
                    discovery.stop()
                    self.assertTrue(discovery.stopped.wait(5))
                for filename in root.glob("*.pid"):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(filename.read_text()), 0)

    def test_letterboxing_rotation_and_invalid_dimensions(self):
        self.assertIsNone(phone_coordinates(10, 200, (400, 400), (200, 400), (1080, 2160)))
        self.assertEqual(
            phone_coordinates(200, 200, (400, 400), (200, 400), (1080, 2160)), (540, 1080)
        )
        self.assertEqual(
            phone_coordinates(200, 100, (400, 200), (400, 200), (1080, 2160)), (1080, 540)
        )
        self.assertIsNone(phone_coordinates(0, 0, (0, 0), (200, 400), (1080, 2160)))

    def test_device_discovery_excludes_daemon_messages_and_preserves_states(self):
        records = parse_android_devices(
            "* daemon started successfully\nList of devices attached\n"
            "synthetic-phone device model:Test_Phone\n"
            "other unauthorized\noffline-phone offline\n"
        )
        self.assertEqual(
            records,
            [
                ("synthetic-phone", "Test Phone", "device"),
                ("other", "Android", "unauthorized"),
                ("offline-phone", "Android", "offline"),
            ],
        )

    def test_iphone_pairing_scope_permissions_and_receiver_options(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "pairing"
            phone = Device("a" * 32, "iphone", "Test phone")
            args = iphone_arguments("/usr/bin/uxplay", phone, directory, 12345, "0012")
            self.assertEqual(args[args.index("-pin") + 1], "0012")
            self.assertIn("host=127.0.0.1 port=12345", args[args.index("-vrtp") + 1])
            self.assertNotIn("-restrict", args)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "clients").stat().st_mode & 0o777, 0o600)
            public_key = base64.b64encode(bytes(32)).decode("ascii")
            (directory / "clients").write_text(f"{public_key},synthetic-iphone,Synthetic iPhone\n")
            args = iphone_arguments("/usr/bin/uxplay", phone, directory, 12345, "0012")
            self.assertEqual(args[-3:], ["-restrict", "-allow", "synthetic-iphone"])
            (directory / "clients").write_text("invalid pairing record\n")
            with self.assertRaises(DeviceError):
                iphone_arguments("/usr/bin/uxplay", phone, directory, 12345, "0012")
            (directory / "clients").write_bytes(b"\xff\xfe")
            with self.assertRaises(DeviceError):
                iphone_arguments("/usr/bin/uxplay", phone, directory, 12345, "0012")

    def test_reconnect_waits_for_previous_session_cleanup(self):
        started, cleanup_started, finish_cleanup = (threading.Event() for _ in range(3))
        first_finished, second_finished = threading.Event(), threading.Event()

        class Session(Transport):
            def run(self):
                started.set()
                self.cancelled.wait()

            def cleanup(self):
                cleanup_started.set()
                finish_cleanup.wait(5)

        first = Session(lambda *_: None, first_finished.set)
        second = Session(lambda *_: None, second_finished.set)
        first.start()
        try:
            self.assertTrue(started.wait(3))
            first.stop()
            self.assertTrue(cleanup_started.wait(3))
            started.clear()
            second.start(after=[first])
            self.assertFalse(started.wait(0.1))
            finish_cleanup.set()
            self.assertTrue(first_finished.wait(3))
            self.assertTrue(started.wait(3))
        finally:
            finish_cleanup.set()
            first.stop()
            second.stop()
        self.assertTrue(second_finished.wait(3))


class AndroidTransportTests(unittest.TestCase):
    def test_changed_phone_identity_refuses_video_or_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            finished = threading.Event()
            statuses, received = [], []
            phone = Device("a" * 32, "android", "Test", "synthetic-phone", "different-phone")
            with patch.dict(os.environ, fake_helpers(root)):
                transport = AndroidTransport(
                    phone,
                    received.append,
                    lambda *state: statuses.append(state),
                    lambda *_: None,
                    finished.set,
                )
                transport.start()
                self.assertTrue(finished.wait(5))
            self.assertEqual(received, [])
            self.assertEqual(statuses[0][0], "error")
            self.assertFalse((root / "pushed-server").exists())

    def test_stream_input_shell_quoting_cancellation_and_private_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = fake_helpers(root)
            (root / "fixture.h264").write_bytes(b"synthetic video")
            (root / "slow-frame").touch()
            received, finished = threading.Event(), threading.Event()
            statuses, sizes = [], []
            phone = Device("a" * 32, "android", "Test", "synthetic-phone")
            with patch.dict(os.environ, environment):
                transport = AndroidTransport(
                    phone,
                    lambda data: received.set(),
                    lambda *state: statuses.append(state),
                    sizes.append,
                    finished.set,
                )
                transport.start()
                try:
                    self.assertTrue(received.wait(10), statuses)
                    transport.send("text", "$(not-a-command);' quoted")
                    deadline = time.monotonic() + 5
                    inputs = root / "inputs.jsonl"
                    while not inputs.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertEqual(
                        json.loads(inputs.read_text().splitlines()[0]),
                        ["text", "$(not-a-command);' quoted"],
                    )
                    self.assertEqual(sizes, [(1080, 2160)])
                    self.assertEqual(
                        (root / "pushed-server").read_bytes(),
                        scrcpy_bundle.server_path().read_bytes(),
                    )
                    self.assertEqual((root / "server-version").read_text(), scrcpy_bundle.VERSION)
                finally:
                    transport.stop()
                    self.assertTrue(finished.wait(10))
                self.assertEqual(statuses, [])
                for filename in root.glob("*.pid"):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(filename.read_text()), 0)
                (root / "unauthorized").touch()
                finished.clear()
                failed = AndroidTransport(
                    phone,
                    lambda data: None,
                    lambda *state: statuses.append(state),
                    sizes.append,
                    finished.set,
                )
                failed.start()
                self.assertTrue(finished.wait(10))
                self.assertEqual(statuses[-1][0], "error")
                self.assertNotIn("private helper diagnostic", statuses[-1][1])

    def test_missing_tools_produce_an_actionable_error(self):
        with patch("mini_browser.devices.shutil.which", return_value=None):
            with self.assertRaisesRegex(DeviceError, "Install adb"):
                android_tools()


if __name__ == "__main__":
    unittest.main()
