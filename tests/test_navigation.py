import unittest

from mini_browser.navigation import address_to_uri, is_web_url


class NavigationTests(unittest.TestCase):
    def test_websites_and_local_development(self):
        cases = {
            " example.org/page ": "https://example.org/page",
            "HTTPS://example.org": "https://example.org",
            "localhost:3000/test": "http://localhost:3000/test",
            "127.0.0.1:8080": "http://127.0.0.1:8080",
            "[::1]:8080": "http://[::1]:8080",
            "example.org:8443": "https://example.org:8443",
            "http://example.org": "http://example.org",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(address_to_uri(value), expected)

    def test_searches_are_encoded(self):
        self.assertEqual(address_to_uri("gnome & gtk"), "https://duckduckgo.com/?q=gnome+%26+gtk")

    def test_unsafe_and_invalid_addresses(self):
        for value in [
            "",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "http://",
            "http://a:99999",
            "mailto:person@example.org",
            "[bad-ipv6",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                address_to_uri(value)
        for value in ["javascript:alert(1)", "http://", "https://a:bad", "https://a b"]:
            self.assertFalse(is_web_url(value))
