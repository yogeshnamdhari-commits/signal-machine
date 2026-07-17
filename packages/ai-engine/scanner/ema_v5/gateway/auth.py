"""
EMA_V5 Authentication — API key management and validation.
Isolated from existing authentication systems.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger


@dataclass
class APIKey:
    """API key record."""
    key_id: str = ""
    key_hash: str = ""
    name: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    permissions: Set[str] = field(default_factory=lambda: {"read"})
    rate_limit: int = 100  # requests per minute
    active: bool = True


class EMAv5Auth:
    """API key authentication for EMA_V5."""

    def __init__(self) -> None:
        self._keys: Dict[str, APIKey] = {}  # key_id → APIKey
        self._key_hashes: Dict[str, str] = {}  # hash → key_id
        self._default_key = self._generate_key()

    def _generate_key(self) -> str:
        """Generate a new API key."""
        return f"ev5_{secrets.token_hex(32)}"

    def _hash_key(self, key: str) -> str:
        """Hash an API key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def create_key(self, name: str, permissions: Optional[Set[str]] = None,
                   rate_limit: int = 100, expires_in_hours: Optional[int] = None) -> Dict[str, Any]:
        """Create a new API key."""
        key = self._generate_key()
        key_id = secrets.token_hex(8)
        key_hash = self._hash_key(key)

        expires_at = 0.0
        if expires_in_hours:
            expires_at = time.time() + (expires_in_hours * 3600)

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            created_at=time.time(),
            expires_at=expires_at,
            permissions=permissions or {"read"},
            rate_limit=rate_limit,
        )

        self._keys[key_id] = api_key
        self._key_hashes[key_hash] = key_id

        logger.info("EMAv5 API key created: {} ({})", key_id, name)

        return {
            "key_id": key_id,
            "key": key,  # Only shown once
            "name": name,
            "permissions": list(api_key.permissions),
            "rate_limit": api_key.rate_limit,
            "expires_at": expires_at,
        }

    def validate_key(self, key: str) -> Dict[str, Any]:
        """Validate an API key."""
        if not key:
            return {"authenticated": False, "message": "No key provided"}

        key_hash = self._hash_key(key)
        key_id = self._key_hashes.get(key_hash)

        if not key_id:
            return {"authenticated": False, "message": "Invalid API key"}

        api_key = self._keys.get(key_id)
        if not api_key:
            return {"authenticated": False, "message": "Key not found"}

        if not api_key.active:
            return {"authenticated": False, "message": "Key deactivated"}

        if api_key.expires_at > 0 and time.time() > api_key.expires_at:
            return {"authenticated": False, "message": "Key expired"}

        return {
            "authenticated": True,
            "key_id": key_id,
            "name": api_key.name,
            "permissions": list(api_key.permissions),
            "rate_limit": api_key.rate_limit,
        }

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        api_key = self._keys.get(key_id)
        if not api_key:
            return False

        api_key.active = False
        if api_key.key_hash in self._key_hashes:
            del self._key_hashes[api_key.key_hash]

        logger.info("EMAv5 API key revoked: {}", key_id)
        return True

    def get_key_info(self, key_id: str) -> Optional[Dict]:
        """Get key information."""
        api_key = self._keys.get(key_id)
        if not api_key:
            return None

        return {
            "key_id": api_key.key_id,
            "name": api_key.name,
            "created_at": api_key.created_at,
            "expires_at": api_key.expires_at,
            "permissions": list(api_key.permissions),
            "rate_limit": api_key.rate_limit,
            "active": api_key.active,
        }

    def list_keys(self) -> List[Dict]:
        """List all API keys."""
        return [
            {
                "key_id": k.key_id,
                "name": k.name,
                "active": k.active,
                "permissions": list(k.permissions),
            }
            for k in self._keys.values()
        ]

    def has_permission(self, key: str, permission: str) -> bool:
        """Check if a key has a specific permission."""
        result = self.validate_key(key)
        if not result["authenticated"]:
            return False
        return permission in result.get("permissions", [])
