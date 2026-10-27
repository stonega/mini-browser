"""Process cleanup checks tolerate a process exiting during a /proc read."""

import unittest
from pathlib import Path
from unittest.mock import patch

from harness_helper import running


class HarnessProcessTests(unittest.TestCase):
    def test_process_disappearing_during_stat_read_is_stopped(self):
        with patch.object(Path, "read_text", side_effect=ProcessLookupError()):
            self.assertFalse(running(12345))
