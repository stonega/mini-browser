import unittest

from mini_browser.gtk import Gdk, Web


class KeyboardTests(unittest.TestCase):
    def test_browser_shortcuts_require_the_registered_modifiers(self):
        control = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        alt = Gdk.ModifierType.ALT_MASK
        for value, state in (
            (Gdk.KEY_i, control),
            (Gdk.KEY_k, control),
            (Gdk.KEY_p, control),
            (Gdk.KEY_r, control | alt),
            (Gdk.KEY_n, control | shift),
            (Gdk.KEY_F5, shift),
            (Gdk.KEY_Left, alt | shift),
        ):
            with self.subTest(value=value, state=state):
                self.assertFalse(Web.View._host_shortcut(value, state))
        for value, state in (
            (Gdk.KEY_i, control | shift),
            (Gdk.KEY_k, control | shift),
            (Gdk.KEY_p, control | shift),
            (Gdk.KEY_r, control),
            (Gdk.KEY_r, control | shift),
            (Gdk.KEY_F5, control),
            (Gdk.KEY_F5, 0),
            (Gdk.KEY_Left, alt),
            (Gdk.KEY_plus, control | shift),
            (Gdk.KEY_question, control | shift),
        ):
            with self.subTest(value=value, state=state):
                self.assertTrue(Web.View._host_shortcut(value, state))

    def test_punctuation_and_nontext_keys_use_windows_virtual_codes(self):
        for value, expected in (
            (Gdk.KEY_ISO_Left_Tab, 9),
            (Gdk.KEY_Shift_L, 16),
            (Gdk.KEY_Control_R, 17),
            (Gdk.KEY_Alt_L, 18),
            (Gdk.KEY_F2, 113),
            (Gdk.KEY_F12, 123),
            (Gdk.KEY_semicolon, 186),
            (Gdk.KEY_colon, 186),
            (Gdk.KEY_question, 191),
            (Gdk.KEY_exclam, 49),
        ):
            with self.subTest(value=value):
                self.assertEqual(Web.View._key_code(value), expected)


if __name__ == "__main__":
    unittest.main()
