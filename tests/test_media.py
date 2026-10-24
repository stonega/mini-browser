import unittest

from mini_browser.media import video_window_size


class VideoSizeTests(unittest.TestCase):
    def size(self, width, height, max_width=1152, max_height=819):
        return video_window_size(width, height, max_width=max_width, max_height=max_height)

    def test_intrinsic_landscape_ratios(self):
        self.assertEqual(self.size(1920, 1080), (640, 360))
        self.assertEqual(self.size(800, 600), (640, 480))
        self.assertEqual(self.size(2560, 1080), (640, 270))

    def test_portrait_and_small_monitor(self):
        self.assertEqual(self.size(1080, 1920), (461, 819))
        self.assertEqual(self.size(1920, 1080, max_width=400), (400, 225))
        self.assertEqual(self.size(800, 600, max_height=300), (400, 300))

    def test_metadata_fallback(self):
        for width, height in [
            (0, 0),
            (None, 100),
            (100, -1),
            (float("nan"), 1),
            (float("inf"), 100),
        ]:
            with self.subTest(width=width, height=height):
                self.assertEqual(self.size(width, height), (640, 360))
