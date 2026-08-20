"""Account connection store.

Stores only provider connection status and official access tokens supplied
through legitimate OAuth flows. It never stores passwords, cookies, or
session tokens obtained by hijacking.

Token storage uses the OS credential vault (Windows Credential Manager) when
available via `keyring`; if keyring is not installed, tokens are kept in a
JSON file whose permissions are restricted to the current user.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging_setup import app_data_dir

log = logging.getLogger(__name__)

try:
    import keyring  # type: ignore  # noqa: E402
except ImportError:  # pragma: no cover
    keyring = None


@dataclass
class Account:
    provider_id: str
    connected: bool = False
    display_name: str = ""
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class AccountsStore:
    """Persists account connection metadata (never secrets)."""

    SERVICE_NAME = "VideoDownloader"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "accounts.json")
        self._accounts: dict[str, Account] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for provider_id, data in raw.items():
                self._accounts[provider_id] = Account(
                    provider_id=provider_id,
                    connected=bool(data.get("connected")),
                    display_name=data.get("display_name", ""),
                    scopes=list(data.get("scopes", [])),
                    metadata=data.get("metadata", {}),
                )
        except Exception:  # noqa: BLE001
            log.exception("Failed to load accounts file %s", self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            pid: {
                "connected": acc.connected,
                "display_name": acc.display_name,
                "scopes": acc.scopes,
                "metadata": acc.metadata,
            }
            for pid, acc in self._accounts.items()
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    # ------------------------------------------------------------------
    def get(self, provider_id: str) -> Account:
        return self._accounts.setdefault(provider_id, Account(provider_id=provider_id))

    def set_connected(
        self,
        provider_id: str,
        display_name: str = "",
        scopes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Account:
        acc = self.get(provider_id)
        acc.connected = True
        acc.display_name = display_name or provider_id
        acc.scopes = list(scopes or [])
        acc.metadata = dict(metadata or {})
        self._save()
        return acc

    def disconnect(self, provider_id: str) -> Account:
        acc = self.get(provider_id)
        acc.connected = False
        acc.display_name = ""
        acc.scopes = []
        acc.metadata = {}
        self._delete_token(provider_id)
        self._save()
        return acc

    def all(self) -> list[Account]:
        return list(self._accounts.values())

    def is_connected(self, provider_id: str) -> bool:
        return self.get(provider_id).connected

    # -- token helpers (Credential Manager via keyring) -----------------
    def _token_key(self, provider_id: str) -> str:
        return f"{provider_id}.access_token"

    def set_token(self, provider_id: str, token: str) -> None:
        if keyring is None:
            self._set_token_file(provider_id, token)
        else:
            try:
                keyring.set_password(self.SERVICE_NAME, self._token_key(provider_id), token)
            except Exception:  # noqa: BLE001
                log.warning("keyring unavailable, falling back to file storage")
                self._set_token_file(provider_id, token)

    def get_token(self, provider_id: str) -> str:
        if keyring is not None:
            try:
                token = keyring.get_password(self.SERVICE_NAME, self._token_key(provider_id))
                if token:
                    return token
            except Exception:  # noqa: BLE001
                log.warning("keyring read failed for %s", provider_id)
        return self._get_token_file(provider_id)

    def _delete_token(self, provider_id: str) -> None:
        if keyring is not None:
            try:
                keyring.delete_password(
                    self.SERVICE_NAME, self._token_key(provider_id)
                )
            except Exception:  # noqa: BLE001
                pass
        self._delete_token_file(provider_id)

    def _token_file(self) -> Path:
        return app_data_dir() / ".tokens.json"

    def _set_token_file(self, provider_id: str, token: str) -> None:
        path = self._token_file()
        data: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        data[self._token_key(provider_id)] = token
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _get_token_file(self, provider_id: str) -> str:
        path = self._token_file()
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get(self._token_key(provider_id), ""))
        except Exception:  # noqa: BLE001
            return ""

    def _delete_token_file(self, provider_id: str) -> None:
        path = self._token_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        data.pop(self._token_key(provider_id), None)
        path.write_text(json.dumps(data), encoding="utf-8")