"""Command-backed apps use synthetic local servers and private temporary stores."""

import os
import queue
import shlex
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_helper import fake_harness, running, runs

from mini_browser.script_apps import ScriptAppHarness, ScriptAppStore, script_app_values
from mini_browser.storage import Library


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(predicate):
    deadline = time.monotonic() + 6
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic script app did not stop")
        time.sleep(0.02)


class ScriptAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "library.sqlite3")
        self.store = ScriptAppStore(self.library)
        self.port = unused_port()
        self.url = f"http://127.0.0.1:{self.port}/"

    def make_app(self, **config):
        helpers = self.root / "helpers with spaces"
        fake_harness(helpers, use_env_port=True, **config)
        self.helpers = helpers
        return self.store.save(
            "Local app", shlex.join([str(helpers / "npx"), "two words"]), self.port, str(self.root)
        )

    def runner(self, record, **options):
        runner = ScriptAppHarness(record, **options)
        self.addCleanup(runner.close)
        events = queue.Queue()
        owner = object()
        runner.acquire(owner, lambda uri, error: events.put((uri, error)))
        return runner, owner, events

    def test_save_deduplicates_preserves_id_and_is_independent_of_browser_data(self):
        record = self.make_app()
        duplicate = self.store.save(
            "Renamed", record["command"], record["port"], record["directory"]
        )
        self.assertEqual(record["id"], duplicate["id"])
        self.assertEqual(ScriptAppStore(Library(self.library.path)).all(), [duplicate])
        self.assertEqual(self.library.search("history"), [])
        self.assertEqual(self.library.search("bookmarks"), [])
        self.assertEqual(runs(self.helpers), [])
        self.assertTrue(self.store.remove(record["id"]))
        self.assertFalse(self.store.remove(record["id"]))
        self.assertEqual(self.store.all(), [])

    def test_validation_rejects_invalid_commands_names_ports_and_folders(self):
        valid = ["App", "python3 server.py", self.port, str(self.root)]
        for position, value in (
            (0, ""),
            (0, "x" * 201),
            (1, ""),
            (1, "python3 '"),
            (1, "a\0b"),
            (2, "https://example.org/"),
            (2, -1),
            (2, 65536),
            (2, "8000.5"),
            (2, "８０００"),
            (2, True),
            (3, "relative/folder"),
            (3, str(self.root / "missing")),
        ):
            values = valid.copy()
            values[position] = value
            with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                self.store.save(*values)
        self.assertEqual(self.store.all(), [])
        normalized = script_app_values(*valid)
        self.assertEqual(shlex.split(normalized[1]), ["python3", "server.py"])
        for value, expected in (("", 0), (None, 0), (0, 0), ("008000", 8000), (65535, 65535)):
            with self.subTest(port=value):
                record = self.store.save("Port", "python3 server.py", value, str(self.root))
                self.assertEqual(record["port"], expected)

    def test_shared_command_waits_for_configured_url_and_stops_with_last_owner(self):
        record = self.make_app(delay=0.3, child=True, messages=["token=private-fixture-token"])
        runner, owner, events = self.runner(record)
        self.assertEqual(events.get(timeout=6), (self.url, None))
        self.assertTrue(runner.owns_uri(self.url + "another"))
        self.assertFalse(runner.owns_uri("https://example.invalid/"))
        second, other = object(), queue.Queue()
        runner.acquire(second, lambda uri, error: other.put((uri, error)))
        self.assertEqual(other.get(timeout=6), (self.url, None))
        launched = runs(self.helpers)
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0]["args"], ["two words"])
        self.assertEqual(launched[0]["cwd"], str(self.root))
        self.assertEqual(launched[0]["port_env"], str(self.port))
        runner.release(owner)
        self.assertTrue(running(launched[0]["pid"]))
        runner.release(second)
        wait_for(lambda: not running(launched[0]["pid"]))
        for child in self.helpers.glob("child-*"):
            wait_for(lambda child=child: not running(int(child.name.removeprefix("child-"))))

    def test_timeout_and_failed_command_do_not_expose_output(self):
        record = self.make_app(mode="silent", child=True)
        runner, _owner, events = self.runner(record, startup_timeout=0.2)
        uri, error = events.get(timeout=6)
        self.assertIsNone(uri)
        self.assertIn("too long", error)
        runner.close()
        self.assertTrue(all(not running(item["pid"]) for item in runs(self.helpers)))
        self.make_app(mode="fail")
        _runner, _owner, events = self.runner(record)
        uri, error = events.get(timeout=6)
        self.assertIsNone(uri)
        self.assertIn("stopped", error)
        self.assertNotIn("private-fixture-token", error)

    def test_existing_url_records_retain_their_port_path_and_query(self):
        original = self.make_app()
        with self.library.connect() as db:
            db.execute(
                "UPDATE script_apps SET url=? WHERE id=?",
                (self.url + "路径?q=中文#片段", original["id"]),
            )
        record = ScriptAppStore(Library(self.library.path)).all()[0]
        self.assertEqual(record["id"], original["id"])
        self.assertEqual(record["port"], self.port)
        _runner, _owner, events = self.runner(record)
        self.assertEqual(events.get(timeout=6), (record["url"], None))

    def test_automatic_port_is_passed_to_child_and_not_saved_as_fixed(self):
        original = self.make_app()
        record = self.store.save("Auto", original["command"], directory=str(self.root))
        with patch.dict(os.environ, {"PORT": "1234"}):
            runner, owner, events = self.runner(record)
            uri, error = events.get(timeout=6)
            self.assertIsNone(error)
            self.assertGreater(runner.port, 0)
            self.assertEqual(uri, f"http://127.0.0.1:{runner.port}/")
            self.assertEqual(runs(self.helpers)[0]["port_env"], str(runner.port))
            self.assertEqual(os.environ["PORT"], "1234")
            self.assertEqual(record["port"], 0)
            self.assertEqual(ScriptAppStore(Library(self.library.path)).all()[-1], record)
            runner.release(owner)

    def test_two_automatic_apps_can_run_with_distinct_ports(self):
        original = self.make_app(delay=0.2)
        record = self.store.save("Auto", original["command"], directory=str(self.root))
        first, _owner, events = self.runner(record)
        second, _other, other_events = self.runner(record)
        first_url, first_error = events.get(timeout=6)
        second_url, second_error = other_events.get(timeout=6)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertNotEqual(first.port, second.port)
        self.assertNotEqual(first_url, second_url)

    def test_port_claim_is_released_after_failed_startup(self):
        record = self.make_app(mode="fail")
        failed, _owner, events = self.runner(record)
        self.assertIsNotNone(events.get(timeout=6)[1])
        failed.close()
        self.make_app()
        _runner, _owner, events = self.runner(record)
        self.assertEqual(events.get(timeout=6), (self.url, None))

    def test_occupied_url_refuses_to_launch_or_open_unrelated_server(self):
        record = self.make_app()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            _runner, _owner, events = self.runner(record)
            uri, error = events.get(timeout=6)
            self.assertIsNone(uri)
            self.assertIn("already in use", error)
            self.assertEqual(runs(self.helpers), [])

    def test_missing_command_has_actionable_error(self):
        record = self.store.save("Missing", "/missing/synthetic-app", self.port, str(self.root))
        _runner, _owner, events = self.runner(record)
        uri, error = events.get(timeout=6)
        self.assertIsNone(uri)
        self.assertIn("command was not found", error)
