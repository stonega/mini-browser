import json
import unittest

from mini_browser.page_context import agent_prompt, menu_groups, search_url


class PageContextTests(unittest.TestCase):
    def test_plain_page_has_page_actions_without_edit_actions(self):
        actions = {item.action for group in menu_groups({}) for item in group}
        self.assertTrue({"agent", "save-page", "save-pdf", "source", "bookmark"} <= actions)
        self.assertFalse({"copy-link", "copy-media", "search"} & actions)

    def test_linked_image_keeps_both_contexts_and_blocks_non_web_navigation(self):
        groups = menu_groups(
            {"link_url": "javascript:alert(1)", "media_type": 1, "source_url": "data:image/png,a"}
        )
        items = {item.action: item for group in groups for item in group}
        for action in ("open-link", "save-link", "open-media", "save-media"):
            self.assertFalse(items[action].enabled)
        self.assertTrue(items["copy-link"].enabled)
        self.assertTrue(items["copy-media"].enabled)
        self.assertTrue(items["agent"].enabled)

    def test_editable_field_has_agent_and_native_edit_state_without_page_saving(self):
        groups = menu_groups(
            {
                "editable": True,
                "items": [
                    {"id": 112, "label": "Cut", "enabled": False},
                    {"separator": True},
                    {"id": 114, "label": "Paste", "enabled": True},
                ],
            }
        )
        items = {item.action: item for group in groups for item in group}
        self.assertFalse(items[112].enabled)
        self.assertTrue(items[114].enabled)
        self.assertIn("agent", items)
        self.assertNotIn("save-page", items)

    def test_agent_prompt_preserves_selection_image_and_frame_as_data(self):
        selected = "Synthetic text\n剪貼簿 `$(touch /tmp/do-not-run)`\n```"
        context = {
            "page_url": "https://example.test/article?token=synthetic",
            "frame_url": "https://frame.test/embedded",
            "selection": selected,
            "media_type": 1,
            "source_url": "https://example.test/image.png",
            "link_url": "https://example.test/link",
        }
        prompt = agent_prompt(context, title="Article", page_id=17)
        data = json.loads(prompt[prompt.index("{\n") :])
        self.assertEqual(data["selected_text"], selected)
        self.assertEqual(data["image_url"], context["source_url"])
        self.assertEqual(data["frame_url"], context["frame_url"])
        instructions = prompt[: prompt.index("{\n")]
        self.assertNotIn(selected, instructions)
        self.assertIn("agent list", instructions)
        self.assertIn("agent snapshot PAGE", instructions)
        self.assertIn("document.body.innerText", instructions)
        self.assertIn("17", instructions)

    def test_page_prompt_omits_absent_selection_and_image(self):
        prompt = agent_prompt({"page_url": "https://example.test/"}, title="Page", page_id=2)
        data = json.loads(prompt[prompt.index("{\n") :])
        self.assertEqual(data, {"title": "Page", "url": "https://example.test/"})

    def test_selection_search_encodes_query_as_data(self):
        self.assertEqual(
            search_url("a&b #剪"), "https://www.google.com/search?q=a%26b%20%23%E5%89%AA"
        )


if __name__ == "__main__":
    unittest.main()
