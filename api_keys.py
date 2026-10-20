"""Keychain-backed Gemini credentials and bounded, quota-aware failover."""

import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field

import Security
from google import genai
from google.genai import types


class KeychainError(OSError):
    pass


class KeychainStore:
    def __init__(self, service="local.sobes.assistant.api-keys"):
        self._query = {Security.kSecClass: Security.kSecClassGenericPassword,
                       Security.kSecAttrService: service, Security.kSecAttrAccount: "profiles-v1"}

    def load(self):
        status, data = Security.SecItemCopyMatching({**self._query, Security.kSecReturnData: True,
                                                    Security.kSecMatchLimit: Security.kSecMatchLimitOne}, None)
        if status == Security.errSecItemNotFound:
            return {"entries": [], "active": None, "auto_switch": True}
        if status != Security.errSecSuccess:
            raise KeychainError(f"Не удалось прочитать Связку ключей macOS (код {status}).")
        try:
            result = json.loads(bytes(data).decode("utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("entries"), list):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise KeychainError("Запись API-ключей в Связке повреждена. Она не была изменена.") from None

    def save(self, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        status = Security.SecItemUpdate(self._query, {Security.kSecValueData: payload})
        if status == Security.errSecItemNotFound:
            status, _ = Security.SecItemAdd({**self._query, Security.kSecValueData: payload}, None)
        if status != Security.errSecSuccess:
            raise KeychainError(f"Не удалось сохранить ключи в Связке macOS (код {status}).")


@dataclass(frozen=True)
class ApiKey:
    id: str
    name: str
    secret: str = field(repr=False)
    project: str = ""


def error_code(error):
    code = getattr(error, "code", None)
    if isinstance(code, int):
        if code in (1007, 1008, 1011):
            reason = str(getattr(error, "reason", "")) + str(getattr(error, "rcvd", ""))
            if "RESOURCE_EXHAUSTED" in reason or "429" in reason:
                return 429
            if "UNAUTHENTICATED" in reason or "API_KEY_INVALID" in reason:
                return 401
            if "PERMISSION_DENIED" in reason:
                return 403
        return code
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None)


def credential_error(error):
    code = error_code(error)
    return code in (401, 403, 429) or (code == 400 and "API_KEY_INVALID" in str(error_payload(error)))


def error_payload(error):
    payload = getattr(error, "details", None) or getattr(error, "response_json", {}) or {}
    return {"details": payload} if isinstance(payload, list) else payload


def retry_seconds(error):
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        delay = float(headers.get("retry-after", ""))
        if math.isfinite(delay) and delay > 0:
            return max(1, delay)
    except (ValueError, TypeError):
        pass
    payload = error_payload(error)
    if isinstance(payload, dict):
        body = payload.get("error", payload)
        details = body.get("details", []) if isinstance(body, dict) else []
        for detail in details:
            if isinstance(detail, dict) and "retryDelay" in detail:
                try:
                    seconds = float(str(detail["retryDelay"]).removesuffix("s"))
                    if math.isfinite(seconds) and seconds > 0:
                        return max(1, seconds)
                except ValueError:
                    pass
    # Respect daily limits even if RetryInfo is missing.
    if "PerDay" in str(payload):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, reset.timestamp() - now.timestamp())
    return 60


class KeyPool:
    def __init__(self, store, environment_key=None, on_change=None):
        self._store = store
        self._lock = threading.RLock()
        self._clients = {}
        self._retired = []
        self._cooldown = {}
        self._invalid = set()
        self._denied = set()
        self._secrets = set()
        self.on_change = on_change
        data = store.load()
        try:
            self._entries = [ApiKey(**item) for item in data["entries"]]
            self._validate(self._entries)
        except (TypeError, ValueError):
            raise KeychainError("Список API-ключей в Связке повреждён. Он не был изменён.") from None
        self._active = data.get("active")
        self.auto_switch = bool(data.get("auto_switch", True))
        if not self._entries and environment_key:
            self._entries = [ApiKey("environment", "Из окружения — сохраните в настройках", environment_key)]
        self._secrets.update(entry.secret for entry in self._entries)

    @staticmethod
    def _validate(entries):
        if len({entry.id for entry in entries}) != len(entries) or len({entry.secret for entry in entries}) != len(entries):
            raise ValueError("Этот API-ключ уже добавлен.")
        for entry in entries:
            if (not all(isinstance(value, str) for value in (entry.id, entry.name, entry.secret, entry.project))
                    or not entry.id or not entry.name.strip() or len(entry.secret) < 16
                    or any(character.isspace() for character in entry.secret)):
                raise ValueError("Укажите название и полный API-ключ без пробелов.")

    def entries(self):
        with self._lock:
            return list(self._entries)

    @property
    def active_id(self):
        with self._lock:
            return self._active if any(entry.id == self._active for entry in self._entries) else (
                self._entries[0].id if self._entries else None)

    def replace(self, entries, active, auto_switch):
        self._validate(entries)
        if entries and active not in {entry.id for entry in entries}:
            raise ValueError("Выберите активный ключ.")
        with self._lock:
            self._store.save({"entries": [asdict(entry) for entry in entries], "active": active,
                              "auto_switch": bool(auto_switch)})
            new = {entry.id: entry for entry in entries}
            changed = {entry.id for entry in self._entries
                       if entry.id not in new or entry.secret != new[entry.id].secret}
            for entry_id in changed:
                if entry_id in self._clients:
                    self._retired.append(self._clients.pop(entry_id))
            self._invalid.difference_update(changed | {active})
            self._denied = {key for key in self._denied if key[0] not in changed | {active}}
            self._cooldown = {key: value for key, value in self._cooldown.items() if key[0] not in changed}
            self._entries, self._active, self.auto_switch = list(entries), active, bool(auto_switch)
            self._secrets.update(entry.secret for entry in entries)
        self._changed()

    def candidates(self, model):
        with self._lock:
            entries = sorted(self._entries, key=lambda entry: entry.id != self._active)
            if not self.auto_switch:
                entries = entries[:1]
            return [entry for entry in entries if entry.id not in self._invalid
                    and (entry.id, model) not in self._denied
                    and self._cooldown.get((entry.id, model), 0) <= time.monotonic()]

    def client(self, entry):
        with self._lock:
            if entry not in self._entries:
                raise ValueError("API-ключ изменён. Повторите запрос.")
            if entry.id not in self._clients:
                self._clients[entry.id] = genai.Client(api_key=entry.secret, http_options=types.HttpOptions(
                    timeout=20_000, retry_options=types.HttpRetryOptions(attempts=1),
                ))
            return self._clients[entry.id]

    def succeeded(self, entry):
        with self._lock:
            self._active = entry.id
        self._changed()

    def failed(self, entry, model, error):
        with self._lock:
            if error_code(error) == 429:
                deadline = time.monotonic() + retry_seconds(error)
                for other in self._entries:
                    if other.id == entry.id or (entry.project and other.project == entry.project):
                        self._cooldown[(other.id, model)] = deadline
            elif error_code(error) == 403:
                self._denied.add((entry.id, model))
            elif credential_error(error):
                self._invalid.add(entry.id)
        self._changed()

    def failed_client(self, client, model, error):
        with self._lock:
            entry = next((entry for entry in self._entries if self._clients.get(entry.id) is client), None)
        if entry and credential_error(error):
            self.failed(entry, model, error)

    def status(self):
        with self._lock:
            if not self._entries:
                return "API-ключи не добавлены — откройте настройки"
            active = next((entry.name for entry in self._entries if entry.id == self._active), self._entries[0].name)
            return self.redact(f"Ключ: {active} · всего {len(self._entries)} · автосмена {'вкл.' if self.auto_switch else 'выкл.'}")

    def unavailable_message(self):
        with self._lock:
            if not self._entries:
                return "Добавьте API-ключ: Настройки → API-ключи…"
            delays = [deadline - time.monotonic() for deadline in self._cooldown.values()
                      if deadline > time.monotonic()]
            if delays:
                return f"Ключи временно недоступны по лимиту. Повторите не раньше чем через {math.ceil(min(delays))} с."
            return "Нет доступных API-ключей. Проверьте ключи и разрешения в настройках."

    def redact(self, message):
        with self._lock:
            for secret in self._secrets:
                message = str(message).replace(secret, "[API-ключ скрыт]")
        return re.sub(r"AIza[\w-]{20,}", "[API-ключ скрыт]", str(message))

    def _changed(self):
        if self.on_change:
            self.on_change()

    def close(self):
        with self._lock:
            clients = list(self._clients.values()) + self._retired
        for client in clients:
            client.close()
