import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mini_browser.storage import Library
from mini_browser.window_size import DEFAULT_SIZE, WindowSizeMemory, script_app_key, web_app_key


class FakeWindow:
    def __init__(self):
        self.size = DEFAULT_SIZE
        self.callbacks = []
        self.toast = Mock()

    def connect(self, _signal, callback):
        self.callbacks.append(callback)

    def get_default_size(self):
        return self.size

    def set_default_size(self, width, height):
        self.size = (width, height)
        for callback in self.callbacks:
            callback()


class WindowSizeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.sqlite3"
        self.library = Library(self.path)
        self.timer = patch("mini_browser.window_size.GLib.timeout_add", return_value=123).start()
        patch("mini_browser.window_size.GLib.source_remove").start()
        self.addCleanup(patch.stopall)
        self.window = FakeWindow()
        self.memory = WindowSizeMemory(self.window, self.library)
        self.addCleanup(self.memory.close)

    def test_web_keys_use_hostname_and_keep_subdomains_distinct(self):
        key = ("web", "sds.example.com")
        for uri in (
            "https://sds.example.com/sdajoij",
            "http://SDS.EXAMPLE.COM:8080/other?query=value#fragment",
            "https://sds.example.com./",
        ):
            self.assertEqual(web_app_key(uri), key)
        self.assertNotEqual(web_app_key("https://example.com"), key)
        self.assertNotEqual(web_app_key("https://other.example.com"), key)
        self.assertEqual(web_app_key("https://bücher.example"), ("web", "xn--bcher-kva.example"))
        self.assertEqual(web_app_key("http://[::1]:8000/a"), ("web", "::1"))
        for uri in ("", "file:///tmp/page", "https://..", "https://[bad", None):
            self.assertIsNone(web_app_key(uri))

    def test_persistence_and_namespaces_across_library_reopen(self):
        for kind, size in (("web", (600, 700)), ("device", (500, 800)), ("script", (900, 650))):
            self.library.save_window_size(kind, "same-key", *size)
            self.assertEqual(Library(self.path).window_size(kind, "same-key"), size)
        self.assertEqual(self.library.search("history"), [])
        self.assertEqual(self.library.recent_apps(), [])

    def test_existing_library_is_upgraded_without_changing_history(self):
        self.library.record_visit("https://example.com/", "Synthetic")
        with self.library.connect() as db:
            db.execute("DROP TABLE window_sizes")
        upgraded = Library(self.path)
        self.assertIsNone(upgraded.window_size("web", "example.com"))
        self.assertEqual(upgraded.search("history")[0]["title"], "Synthetic")

    def test_invalid_sizes_are_rejected_or_ignored(self):
        for size in ((0, 700), (600, -1), (True, 700), (600.5, 700), (2**31, 700)):
            with self.assertRaises(ValueError):
                self.library.save_window_size("web", "example.com", *size)
        with self.library.connect() as db:
            db.execute("INSERT INTO window_sizes VALUES ('web', 'bad', 1.5, 700)")
        self.assertIsNone(self.library.window_size("web", "bad"))

    def test_switch_flushes_resizes_and_home_does_not_overwrite_site(self):
        first, second = ("web", "first.example"), ("web", "second.example")
        self.memory.select(first)
        self.window.set_default_size(630, 710)
        self.memory.select(second)
        self.assertEqual(self.library.window_size(*first), (630, 710))
        self.assertEqual(self.window.size, DEFAULT_SIZE)
        self.window.set_default_size(720, 650)
        self.memory.select(None)
        self.window.set_default_size(500, 500)
        self.memory.select(first)
        self.assertEqual(self.window.size, (630, 710))
        self.memory.select(second)
        self.assertEqual(self.window.size, (720, 650))

    def test_same_host_paths_do_not_reset_resized_window(self):
        self.memory.select(web_app_key("https://example.com/first"))
        self.window.set_default_size(640, 720)
        self.memory.select(web_app_key("http://example.com:8080/second"))
        self.assertEqual(self.window.size, (640, 720))
        self.memory.close()
        restored = FakeWindow()
        memory = WindowSizeMemory(restored, Library(self.path))
        self.addCleanup(memory.close)
        memory.select(web_app_key("https://example.com/third"))
        self.assertEqual(restored.size, (640, 720))

    def test_script_keys_track_commands_independently(self):
        first = script_app_key(("npx", "--yes", "example", "web"))
        self.assertEqual(first, script_app_key(["npx", "--yes", "example", "web"]))
        second = script_app_key(("npx", "--yes", "another", "web"))
        self.memory.select(first)
        self.window.set_default_size(850, 650)
        self.memory.select(second)
        self.assertEqual(self.window.size, DEFAULT_SIZE)
        self.memory.select(first)
        self.assertEqual(self.window.size, (850, 650))

    def test_media_layout_and_close_keep_normal_size(self):
        self.memory.select(("web", "example.com"))
        self.window.set_default_size(650, 720)
        self.memory.capture_size = lambda: None
        self.window.set_default_size(420, 200)
        self.memory.close()
        self.assertEqual(self.library.window_size("web", "example.com"), (650, 720))
        self.assertEqual(self.memory.source, 0)

    def test_unchanged_window_close_does_not_overwrite_newer_size(self):
        key = ("device", "synthetic-device")
        self.library.save_window_size(*key, 600, 700)
        self.memory.select(key)
        self.library.save_window_size(*key, 650, 730)
        self.memory.close()
        self.assertEqual(self.library.window_size(*key), (650, 730))

    def test_failed_writes_can_retry_and_failed_reads_use_default(self):
        key = ("device", "synthetic-device")
        self.memory.select(key)
        self.window.set_default_size(620, 730)
        with patch.object(self.library, "save_window_size", side_effect=sqlite3.OperationalError):
            self.memory.save()
        self.window.toast.assert_called_with("Could not save this window size.")
        self.assertTrue(self.memory.dirty)
        self.memory.save()
        self.assertEqual(self.library.window_size(*key), (620, 730))
        with patch.object(self.library, "window_size", side_effect=sqlite3.OperationalError):
            self.memory.select(("device", "another-device"))
        self.assertEqual(self.window.size, DEFAULT_SIZE)
        self.window.toast.assert_called_with("Could not restore the saved window size.")


if __name__ == "__main__":
    unittest.main()
