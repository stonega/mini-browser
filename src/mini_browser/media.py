"""Window sizing independent of GTK and the display server."""

import math

DEFAULT_VIDEO_WIDTH = 640
DEFAULT_VIDEO_HEIGHT = 360


def video_window_size(width, height, *, max_width, max_height):
    """Fit the intrinsic video ratio within the monitor without locking resizing."""
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in (width, height)
    ):
        width, height = DEFAULT_VIDEO_WIDTH, DEFAULT_VIDEO_HEIGHT
    scale = min(DEFAULT_VIDEO_WIDTH / width, max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))
