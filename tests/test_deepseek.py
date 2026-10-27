import os
import queue
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

from harness_helper import fake_harness, running, runs

from mini_browser.deepseek import COMMAND, DeepSeekHarness, startup_uri


def wait_for(predicate):
    deadline = time.monotonic() + 6
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for synthetic helper cleanup")
        time.sleep(0.02)


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = fake_harness(self.root)
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.harness = DeepSeekHarness(startup_timeout=5)
        self.addCleanup(self.harness.close)

    def acquire(self, owner=None):
        owner = owner or object()
        events = queue.Queue()
        self.harness.acquire(owner, lambda uri, error: events.put((uri, error)))
        return owner, events

    def assert_stopped(self):
        wait_for(
            lambda: all(
                not running(item["pid"]) and not running(item["group"]) for item in runs(self.root)
            )
        )
        for path in self.root.glob("child-*"):
            wait_for(lambda path=path: not running(int(path.name.removeprefix("child-"))))

    def test_shared_server_and_last_owner_cleanup(self):
        first, events = self.acquire()
        uri, error = events.get(timeout=6)
        self.assertIsNone(error)
        second, other = self.acquire()
        self.assertEqual(other.get(timeout=6), (uri, None))
        self.assertEqual(len(runs(self.root)), 1)
        self.assertEqual(runs(self.root)[0]["args"], list(COMMAND[1:]))
        self.assertEqual(runs(self.root)[0]["cwd"], str(Path.home()))
        self.assertTrue(self.harness.owns_uri(uri.split("?", 1)[0] + "session"))
        self.harness.release(first)
        with build_opener(ProxyHandler({})).open(uri, timeout=2) as response:
            self.assertIn(b"Harness fixture", response.read())
        self.assertTrue(events.empty())
        self.harness.release(second)
        self.assert_stopped()

    def test_cancel_startup_and_immediate_reopen(self):
        fake_harness(self.root, delay=0.4, child=True)
        first, abandoned = self.acquire()
        wait_for(lambda: len(runs(self.root)) == 1)
        old_pid = runs(self.root)[0]["pid"]
        self.harness.release(first)
        second, events = self.acquire()
        uri, error = events.get(timeout=6)
        self.assertIsNone(error)
        self.assertIsNotNone(uri)
        self.assertFalse(running(old_pid))
        self.assertTrue(abandoned.empty())
        self.assertEqual(len(runs(self.root)), 2)
        self.harness.release(second)
        self.assert_stopped()

    def test_timeout_reaps_unresponsive_descendants(self):
        fake_harness(self.root, mode="silent", child=True)
        self.harness.startup_timeout = 0.3
        _owner, events = self.acquire()
        uri, error = events.get(timeout=6)
        self.assertIsNone(uri)
        self.assertIn("too long", error)
        self.assert_stopped()

    def test_exit_reports_safe_error_and_can_retry(self):
        fake_harness(self.root, mode="fail")
        owner, events = self.acquire()
        uri, error = events.get(timeout=6)
        self.assertIsNone(uri)
        self.assertIn("terminal", error)
        self.assertNotIn("private-fixture-token", error)
        self.harness.release(owner)
        fake_harness(self.root)
        _owner, events = self.acquire()
        uri, error = events.get(timeout=6)
        self.assertIsNotNone(uri)
        self.assertIsNone(error)
        pid = next(item["pid"] for item in runs(self.root) if running(item["pid"]))
        os.kill(pid, signal.SIGTERM)
        self.assertIn("stopped", events.get(timeout=6)[1])
        self.assert_stopped()

    def test_missing_npx_can_retry(self):
        with patch("mini_browser.deepseek.shutil.which", return_value=None):
            owner, events = self.acquire()
            self.assertIn("Install Node.js", events.get(timeout=6)[1])
        self.harness.release(owner)
        _owner, events = self.acquire()
        self.assertIsNotNone(events.get(timeout=6)[0])

    def test_shutdown_during_startup_and_after_ready(self):
        for delay in (0.4, 0):
            with self.subTest(delay=delay):
                fake_harness(self.root, delay=delay, child=True)
                self.harness = DeepSeekHarness(startup_timeout=5)
                self.addCleanup(self.harness.close)
                _owner, events = self.acquire()
                wait_for(lambda: any(running(item["pid"]) for item in runs(self.root)))
                if not delay:
                    self.assertIsNotNone(events.get(timeout=6)[0])
                self.harness.close()
                self.assert_stopped()
                self.assertFalse(self.harness._thread.is_alive())

    def test_abrupt_app_exit_stops_process_group(self):
        fake_harness(self.root, child=True)
        program = """
import os, time
from mini_browser.deepseek import DeepSeekHarness
harness = DeepSeekHarness()
def ready(uri, error):
    os._exit(0 if uri else 1)
harness.acquire(object(), ready)
time.sleep(15)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(len(runs(self.root)), 1)
            self.assert_stopped()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_only_loopback_ready_announcements_are_accepted(self):
        accepted = "http://127.0.0.1:3080/?token=synthetic"
        self.assertEqual(startup_uri(f"\x1b[32mdsh web: {accepted}\x1b[0m"), accepted)
        for value in (
            "https://example.org:3080/",
            "http://127.0.0.1.evil:3080/",
            "http://user@127.0.0.1:3080/",
            "http://127.0.0.1:70000/",
            "http://127.0.0.1/",
            "http://127.0.0.1:3080/other",
            "http://127.0.0.1:3080/#fragment",
            "http://[invalid:3080/",
            "file:///tmp/index.html",
            "javascript:alert(1)",
            accepted + "\nextra",
        ):
            with self.subTest(value=value):
                self.assertIsNone(startup_uri("dsh web: " + value))
        self.assertIsNone(startup_uri("Download: " + accepted))
