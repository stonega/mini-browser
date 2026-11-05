import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from icon_helper import png

from mini_browser.chromium import View
from mini_browser.site_icon_capture import SiteIconCapture
from mini_browser.site_icons import SiteIconCache


class SiteIconCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = SiteIconCache(Path(temporary.name))
        self.source = "https://youtube.com/"
        self.destination = "https://www.youtube.com/"
        self.view = Mock(generation=1, closed=False, navigation_uri=self.source)
        self.view.get_uri.return_value = self.destination
        self.view.call_async_javascript_function_finish.side_effect = lambda result: result
        self.changed = Mock()
        self.capture = SiteIconCapture(self.view, self.cache, self.changed)

    def discover(self, icons=None):
        self.capture.capture()
        callback = self.view.call_async_javascript_function.call_args.args[-1]
        data = {"uri": self.destination, "icons": icons or [self.destination + "icon.png"]}
        callback(self.view, SimpleNamespace(to_json=lambda _: json.dumps(data)))

    def downloaded(self, data=None):
        callback = self.view.command.call_args.args[2]
        callback({"data": base64.b64encode(png()).decode()} if data is None else data, None)

    def test_redirect_updates_both_origins_even_when_final_icon_is_unchanged(self):
        self.cache.save(self.destination, png())
        self.discover()
        self.downloaded()
        self.assertEqual(self.cache.path_for(self.source).read_bytes(), png())
        self.assertEqual(self.cache.path_for(self.destination).read_bytes(), png())
        self.changed.assert_called_once_with()
        self.changed.reset_mock()
        self.discover()
        self.downloaded()
        self.changed.assert_not_called()

    def test_invalid_image_falls_back_before_saving_both_origins(self):
        icons = [self.destination + "broken.png", self.destination + "icon.png"]
        self.discover(icons)
        self.downloaded({"data": "invalid base64"})
        self.assertEqual(self.view.command.call_args.args[1]["url"], icons[1])
        self.downloaded()
        self.assertEqual(self.cache.path_for(self.source).read_bytes(), png())

    def test_failed_launch_origin_write_still_refreshes_the_final_icon(self):
        save = self.cache.save

        def fail_alias(url, data):
            if url == self.source:
                raise OSError("Synthetic write failure")
            return save(url, data)

        self.discover()
        with patch.object(self.cache, "save", side_effect=fail_alias):
            self.downloaded()
        self.assertEqual(self.cache.path_for(self.destination).read_bytes(), png())
        self.assertIsNone(self.cache.path_for(self.source))
        self.changed.assert_called_once_with()

    def test_stale_downloads_never_update_either_origin(self):
        for change in ("navigation", "generation", "uri", "closed", "cancelled"):
            with self.subTest(change=change):
                self.setUp()
                self.discover()
                if change == "navigation":
                    self.view.navigation_uri = "https://other.example/"
                elif change == "generation":
                    self.view.generation += 1
                elif change == "uri":
                    self.view.get_uri.return_value = "https://other.example/"
                elif change == "closed":
                    self.view.closed = True
                else:
                    self.capture.cancel()
                self.downloaded()
                self.assertIsNone(self.cache.path_for(self.source))
                self.assertIsNone(self.cache.path_for(self.destination))
                self.changed.assert_not_called()

    def test_navigation_target_survives_redirects_and_resets_on_next_load(self):
        view = Mock(loading=False, generation=0, uri="https://previous.example/")
        View._start_load(view, self.source)
        view.uri = self.destination
        View._start_load(view)
        self.assertEqual(view.navigation_uri, self.source)
        View._start_load(view, "https://next.example/")
        self.assertEqual(view.navigation_uri, "https://next.example/")
        view.loading = False
        View._start_load(view)
        self.assertEqual(view.navigation_uri, self.destination)
