import csv
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from mini_browser.vault import (
    LOCK_AFTER,
    Credential,
    PasswordVault,
    VaultError,
    can_fill_origin,
    credential_origin,
    read_password_csv,
)

MASTER = "a generated test master password"
SECRET = 'test-only secret, with "quotes"\nand a new line 🔒'


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = PasswordVault(self.root / "private/passwords.vault")
        self.vault.create(MASTER)
        self.csv = self.write_csv(
            "one.csv",
            [
                ["Test Site", "https://example.org/login", "test-user", SECRET],
            ],
        )

    def write_csv(self, name, rows):
        path = self.root / name
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "url", "username", "password"])
            writer.writerows(rows)
        return path

    def test_round_trip_encrypts_every_credential_field(self):
        self.vault.import_csv(self.csv)
        data = self.vault.path.read_bytes()
        for text in [SECRET, "test-user", "example.org", "Test Site", MASTER]:
            self.assertNotIn(text.encode(), data)
        self.assertEqual(self.vault.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.vault.path.parent.stat().st_mode & 0o777, 0o700)
        self.vault.lock()
        with self.assertRaises(VaultError):
            self.vault.credential("https://example.org", "test-user")
        reopened = PasswordVault(self.vault.path)
        reopened.unlock(MASTER)
        self.assertEqual(reopened.credential("https://example.org", "test-user").password, SECRET)
        self.assertNotIn("password", reopened.summaries()[0])
        self.assertNotIn(SECRET, repr(Credential("https://example.org", "u", SECRET)))

    def test_repeated_import_and_updated_password(self):
        first = self.vault.import_csv(self.csv)
        self.assertEqual((first.added, first.updated, first.skipped), (1, 0, 0))
        before = self.vault.path.read_bytes()
        second = self.vault.import_csv(self.csv)
        self.assertEqual((second.added, second.updated), (0, 0))
        self.assertNotEqual(before, self.vault.path.read_bytes())  # Fresh nonce on every write.
        updated = self.write_csv(
            "updated.csv",
            [
                ["Test Site", "https://example.org/another-path", "test-user", "new test secret"],
            ],
        )
        self.assertEqual(self.vault.import_csv(updated).updated, 1)
        self.assertEqual(len(self.vault.summaries()), 1)

    def test_wrong_master_and_tampering_fail_closed(self):
        self.vault.import_csv(self.csv)
        original = self.vault.path.read_bytes()
        self.vault.lock()
        with self.assertRaises(VaultError):
            self.vault.unlock("wrong password")
        self.assertFalse(self.vault.unlocked)
        for position in [0, 8, 24, 40, len(original) - 1]:
            with self.subTest(position=position):
                modified = bytearray(original)
                modified[position] ^= 1
                self.vault.path.write_bytes(modified)
                with self.assertRaises(VaultError):
                    self.vault.unlock(MASTER)
                self.assertFalse(self.vault.unlocked)
        self.vault.path.write_bytes(original)
        self.vault.unlock(MASTER)

    def test_existing_vault_cannot_be_overwritten_by_create(self):
        before = self.vault.path.read_bytes()
        with self.assertRaises(VaultError):
            PasswordVault(self.vault.path).create("another master password")
        self.assertEqual(self.vault.path.read_bytes(), before)

    def test_stale_writer_preserves_other_process_changes(self):
        other = PasswordVault(self.vault.path)
        other.unlock(MASTER)
        self.vault.import_csv(self.csv)
        saved = self.vault.path.read_bytes()
        with self.assertRaisesRegex(VaultError, "another process"):
            other.import_csv(self.csv)
        self.assertFalse(other.unlocked)
        self.assertEqual(saved, self.vault.path.read_bytes())
        other.unlock(MASTER)
        self.assertEqual(len(other.summaries()), 1)

    def test_failed_atomic_replace_preserves_disk_and_memory(self):
        saved = self.vault.path.read_bytes()
        with patch("mini_browser.vault.os.replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                self.vault.import_csv(self.csv)
        self.assertEqual(saved, self.vault.path.read_bytes())
        self.assertEqual(self.vault.summaries(), [])
        self.assertEqual(list(self.vault.path.parent.glob(".vault-*")), [])
        self.vault.import_csv(self.csv)

    def test_automatic_and_manual_lock(self):
        key = self.vault._key
        now = time.monotonic()
        with patch("mini_browser.vault.time.monotonic", return_value=now + LOCK_AFTER + 1):
            self.assertTrue(self.vault.expire())
        self.assertFalse(self.vault.unlocked)
        self.assertEqual(key, bytes(32))
        self.vault.unlock(MASTER)
        self.vault.lock()
        self.assertFalse(self.vault.unlocked)

    def test_master_password_change_and_delete_survive_restart(self):
        self.vault.import_csv(self.csv)
        with self.assertRaises(VaultError):
            self.vault.change_password("wrong current password", "a different master password")
        self.vault.change_password(MASTER, "a different master password")
        self.vault.lock()
        with self.assertRaises(VaultError):
            self.vault.unlock(MASTER)
        self.vault.unlock("a different master password")
        self.assertEqual(len(self.vault.summaries()), 1)
        self.vault.delete("https://example.org", "test-user")
        self.vault.lock()
        self.vault.unlock("a different master password")
        self.assertEqual(self.vault.summaries(), [])

    def test_parallel_imports_do_not_lose_records(self):
        second = self.write_csv(
            "second.csv",
            [
                ["Second", "https://other.example", "other", "another test password"],
            ],
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.vault.import_csv, [self.csv, second]))
        self.assertEqual(sum(result.added for result in results), 2)
        self.assertEqual(len(self.vault.summaries()), 2)

    def test_short_master_password_rejected(self):
        with self.assertRaises(VaultError):
            PasswordVault(self.root / "short.vault").create("short")


class PasswordCSVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "passwords.csv"

    def test_filters_unsafe_rows_and_keeps_last_duplicate(self):
        self.path.write_text(
            "name,url,username,password,note\n"
            "Test,https://example.org/a,user,first,ignored\n"
            "Test,https://EXAMPLE.org:443/b,user,last,ignored\n"
            "Invalid,javascript:alert(1),user,secret,ignored\n"
            "Invalid,https://example.org,user,,ignored\n"
            "No user,https://example.net/,,valid-secret,ignored\n",
            encoding="utf-8",
        )
        records, skipped = read_password_csv(self.path)
        self.assertEqual(len(records), 2)
        self.assertEqual(skipped, 2)
        self.assertEqual(records[0].password, "last")
        self.assertEqual(records[1].username, "")

    def test_bad_csv_errors_do_not_include_contents(self):
        for contents in ["url,username\nsecret,value", "name,url,username,password\n"]:
            self.path.write_text(contents, encoding="utf-8")
            with self.assertRaises(VaultError) as caught:
                read_password_csv(self.path)
            self.assertNotIn("secret,value", str(caught.exception))

    def test_origin_matching_preserves_scheme_host_and_port(self):
        self.assertEqual(credential_origin("https://EXAMPLE.org:443/path"), "https://example.org")
        self.assertEqual(credential_origin("https://example.org:8443"), "https://example.org:8443")
        self.assertEqual(credential_origin("http://[::1]:80"), "http://[::1]")
        self.assertNotEqual(credential_origin("http://example.org"), "https://example.org")
        for value in ["file:///x", "https://user:secret@example.org", "https://faß.de"]:
            with self.assertRaises(VaultError):
                credential_origin(value)
        self.assertTrue(can_fill_origin("https://example.org"))
        self.assertTrue(can_fill_origin("http://127.0.0.1:8080"))
        self.assertFalse(can_fill_origin("http://example.org"))
        self.assertFalse(can_fill_origin("http://localhost.evil.example"))
