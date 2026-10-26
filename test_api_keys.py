import copy
import sys
import unittest
from unittest.mock import Mock, patch

from google.genai import errors

from api_keys import ApiKey, KeychainError, KeychainStore, KeyPool, credential_error, retry_seconds


class MemoryStore:
    def __init__(self, entries=(), active=None, auto_switch=True):
        self.data = {"entries": [entry.__dict__ for entry in entries], "active": active, "auto_switch": auto_switch}

    def load(self):
        return copy.deepcopy(self.data)

    def save(self, data):
        self.data = copy.deepcopy(data)


def limited(delay="120s"):
    return errors.APIError(429, {"error": {"code": 429, "message": "Rate limit",
                                         "details": [{"retryDelay": delay}]}})


class KeyPoolTests(unittest.TestCase):
    def setUp(self):
        self.first = ApiKey("one", "Первый", "test-secret-one-long", "project-a")
        self.second = ApiKey("two", "Второй", "test-secret-two-long", "project-b")
        self.same_project = ApiKey("three", "Третий", "test-secret-three-long", "project-a")
        self.store = MemoryStore([self.first, self.second, self.same_project], "one")
        self.pool = KeyPool(self.store)

    def test_quota_groups_same_project_and_respects_retry_delay(self):
        with patch("api_keys.time.monotonic", return_value=1000):
            self.pool.failed(self.first, "model", limited())
            self.assertEqual(self.pool.candidates("model"), [self.second])
            self.assertEqual(len(self.pool.candidates("other-model")), 3)
        with patch("api_keys.time.monotonic", return_value=1121):
            self.assertEqual(len(self.pool.candidates("model")), 3)

    def test_live_permission_failure_does_not_disable_rest(self):
        self.pool.failed(self.first, "live", errors.APIError(403, {"message": "Permission denied"}))
        self.assertNotIn(self.first, self.pool.candidates("live"))
        self.assertIn(self.first, self.pool.candidates("rest"))

    def test_changed_secret_resets_failures_without_a_cached_client(self):
        self.pool.failed(self.first, "live", limited())
        replacement = ApiKey("one", "New", "replacement-secret-long")
        self.pool.replace([replacement], "one", True)
        self.assertEqual(self.pool.candidates("live"), [replacement])

    def test_auto_switch_can_be_disabled_without_ignoring_cooldown(self):
        self.pool.replace(self.pool.entries(), self.first.id, False)
        self.assertEqual(self.pool.candidates("model"), [self.first])
        self.pool.failed(self.first, "model", limited())
        self.assertEqual(self.pool.candidates("model"), [])

    def test_saving_survives_restart_and_rejects_duplicate_secrets(self):
        self.pool.replace(self.pool.entries(), self.second.id, True)
        restored = KeyPool(self.store)
        self.assertEqual(restored.active_id, self.second.id)
        with self.assertRaises(ValueError):
            self.pool.replace([self.first, ApiKey("different", "Duplicate", self.first.secret)], "one", True)

    def test_removed_credentials_remain_redacted_for_inflight_errors(self):
        self.pool.replace([self.second], self.second.id, True)
        self.assertNotIn(self.first.secret, self.pool.redact("URL?key=" + self.first.secret))
        self.assertNotIn(self.first.secret, repr(self.first))

    def test_keychain_write_failure_keeps_current_credentials(self):
        self.store.save = Mock(side_effect=KeychainError("locked"))
        with self.assertRaises(KeychainError):
            self.pool.replace([self.second], self.second.id, True)
        self.assertEqual(self.pool.entries()[0], self.first)

    def test_editing_secret_uses_a_new_client(self):
        with patch("api_keys.genai.Client") as factory:
            first_client = self.pool.client(self.first)
            replacement = ApiKey("one", "Первый", "replacement-secret-long", "project-a")
            self.pool.replace([replacement, self.second], "one", True)
            factory.return_value = Mock()
            second_client = self.pool.client(replacement)
            self.assertIsNot(first_client, second_client)
            self.assertEqual(factory.call_args.kwargs["api_key"], replacement.secret)

    def test_corrupt_keychain_is_not_overwritten(self):
        self.store.data["entries"] = [{"not": "valid"}]
        with self.assertRaises(KeychainError):
            KeyPool(self.store)

    def test_websocket_quota_close_is_recognized(self):
        error = RuntimeError("socket closed")
        error.code = 1011
        error.reason = "RESOURCE_EXHAUSTED"
        self.assertTrue(credential_error(error))

    def test_retry_info_and_headers_are_honored(self):
        self.assertEqual(retry_seconds(limited("300s")), 300)
        error = limited("3s")
        error.response = Mock(headers={"retry-after": "123"})
        self.assertEqual(retry_seconds(error), 123)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Keychain API')
    def test_secret_is_passed_to_keychain_api_only_as_data(self):
        store = KeychainStore("local.sobes.test")
        with patch("api_keys.Security.SecItemUpdate", return_value=0) as update:
            store.save(self.store.load())
        query, values = update.call_args.args
        self.assertNotIn(self.first.secret, str(query))
        self.assertIn(self.first.secret.encode(), next(iter(values.values())))


if __name__ == "__main__":
    unittest.main()
