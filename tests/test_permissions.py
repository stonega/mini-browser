import tempfile
import unittest
from pathlib import Path

from mini_browser import permissions
from mini_browser.storage import Library


class PermissionModelTests(unittest.TestCase):
    def test_origins_are_normalized_and_credentials_are_rejected(self):
        cases = {
            "https://Example.COM:443/path?q=1": "https://example.com",
            "http://example.com:8080/page": "http://example.com:8080",
            "https://example.com:80": "https://example.com:80",
            "http://[::1]:8080/": "http://[::1]:8080",
            "http://[::1]": "http://[::1]",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(permissions.normalize_origin(value), expected)
        for value in [
            "",
            "null",
            "file:///etc/passwd",
            "ftp://example.com",
            "https://user:secret@example.com",
            "https://example.com:99999",
            "https://",
            "https://exa mple.com",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                permissions.normalize_origin(value)

    def test_origin_host_is_display_safe(self):
        self.assertEqual(permissions.origin_host("https://example.com:8443"), "example.com")
        self.assertEqual(permissions.origin_host("not an origin"), "not an origin")
        self.assertEqual(permissions.origin_host("http://[::1]:8080"), "::1")

    def test_chromium_masks_map_to_known_permissions(self):
        names, unknown = permissions.parse_mask(
            (1 << 2) | (1 << 12) | (1 << 8) | (1 << 15), "permission"
        )
        self.assertEqual(names, ("camera", "microphone", "geolocation", "notifications"))
        self.assertFalse(unknown)
        names, unknown = permissions.parse_mask(1 << 4, "permission")
        self.assertEqual(names, ())
        self.assertTrue(unknown)
        names, unknown = permissions.parse_mask((1 << 2) | (1 << 0), "permission")
        self.assertEqual(names, ("camera",))
        self.assertTrue(unknown)
        names, unknown = permissions.parse_mask((1 << 0) | (1 << 1), "media-permission")
        self.assertEqual(names, ("camera", "microphone"))
        self.assertFalse(unknown)
        names, unknown = permissions.parse_mask(1 << 3, "media-permission")
        self.assertEqual(names, ())
        self.assertTrue(unknown)

    def test_permission_descriptions(self):
        self.assertEqual(permissions.describe_permissions(("camera",)), "camera")
        self.assertEqual(
            permissions.describe_permissions(("camera", "microphone")),
            "camera and microphone",
        )
        self.assertEqual(
            permissions.describe_permissions(("camera", "microphone", "geolocation")),
            "camera, microphone, and location",
        )
        self.assertEqual(permissions.describe_permissions(()), "no permissions")

    def test_managed_permissions_have_devtools_names_and_settings(self):
        self.assertEqual(set(permissions.CDP_NAMES), set(permissions.PERMISSIONS))
        self.assertEqual(
            permissions.CDP_SETTINGS,
            {"ask": "prompt", "allow": "granted", "block": "denied"},
        )


class PermissionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "library.sqlite3"
        self.library = Library(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_choices_persist_and_ask_removes_a_decision(self):
        origin = "https://example.com"
        self.assertIsNone(self.library.permission_decision(origin, "camera"))
        permissions.decide(self.library, origin, ("camera", "microphone"), permissions.ALLOW)
        self.assertEqual(self.library.permission_decision(origin, "camera"), "allow")
        self.assertEqual(self.library.permission_decision(origin, "microphone"), "allow")
        self.library.set_permission(origin, "microphone", permissions.BLOCK)
        self.assertEqual(self.library.permission_decision(origin, "microphone"), "block")
        self.library.set_permission(origin, "microphone", permissions.ASK)
        self.assertIsNone(self.library.permission_decision(origin, "microphone"))

        reopened = Library(self.path)
        self.assertEqual(reopened.permission_decision(origin, "camera"), "allow")

    def test_records_and_clearing_are_scoped_by_origin(self):
        permissions.decide(
            self.library, "https://one.example", ("camera", "geolocation"), permissions.BLOCK
        )
        permissions.decide(
            self.library, "http://two.example:8080", ("notifications",), permissions.ALLOW
        )
        records = self.library.permission_records()
        self.assertEqual(
            [(record["origin"], record["permission"], record["state"]) for record in records],
            [
                ("http://two.example:8080", "notifications", "allow"),
                ("https://one.example", "camera", "block"),
                ("https://one.example", "geolocation", "block"),
            ],
        )
        self.assertEqual(
            [
                record["permission"]
                for record in self.library.permission_records("https://one.example")
            ],
            ["camera", "geolocation"],
        )
        self.assertEqual(self.library.clear_permissions("https://one.example"), 2)
        self.assertEqual(
            [record["origin"] for record in self.library.permission_records()],
            ["http://two.example:8080"],
        )
        self.assertEqual(self.library.clear_permissions(), 1)
        self.assertEqual(self.library.permission_records(), [])

    def test_invalid_choices_never_write(self):
        origin = "https://example.com"
        with self.assertRaises(ValueError):
            permissions.decide(self.library, origin, ("camera",), "sometimes")
        with self.assertRaises(ValueError):
            permissions.decide(self.library, origin, ("screen",), permissions.ALLOW)
        with self.assertRaises(ValueError):
            self.library.set_permission(origin, "camera", "sometimes")
        with self.assertRaises(ValueError):
            self.library.set_permission("", "camera", permissions.ALLOW)
        self.assertEqual(self.library.permission_records(), [])


if __name__ == "__main__":
    unittest.main()
