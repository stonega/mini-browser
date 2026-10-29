"""Session activity stays bounded and excludes untrusted request contents."""

import unittest
from unittest.mock import Mock

from mini_browser.agent_activity import AgentActivity


class AgentActivityTests(unittest.TestCase):
    def test_newest_first_and_bounded_across_pending_completions(self):
        changed = Mock()
        activity = AgentActivity(changed)
        first = activity.start("list_pages")
        for page_id in range(1, 102):
            activity.start("navigate_page", page_id)
        self.assertEqual(len(activity.records), 100)
        self.assertEqual([item.page_id for item in activity.records], list(range(101, 1, -1)))
        activity.finish(first, "Succeeded")
        self.assertEqual(len(activity.records), 100)
        self.assertNotIn(first, activity.records)

    def test_completion_changes_only_once(self):
        for status in ("Succeeded", "Failed", "Timed out"):
            with self.subTest(status=status):
                changed = Mock()
                activity = AgentActivity(changed)
                record = activity.start("fill", 3)
                self.assertEqual(record.status, "Running")
                activity.finish(record, status)
                activity.finish(record, "Succeeded")
                self.assertEqual(record.status, status)
                self.assertEqual(changed.call_count, 2)

    def test_untrusted_method_and_page_are_not_retained(self):
        activity = AgentActivity(Mock())
        for page_id in ("synthetic secret", True, -1, 0, 2**40):
            record = activity.start("synthetic secret", page_id)
            self.assertEqual(record.method, "unknown_command")
            self.assertIsNone(record.page_id)
            self.assertNotIn("synthetic secret", repr(record))
        self.assertEqual(len(AgentActivity(Mock()).records), 0)
