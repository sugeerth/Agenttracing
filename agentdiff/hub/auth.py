"""Who may sign in: users, their password hashes, and the demo account.

Passwords are never stored: each user keeps a salted PBKDF2-SHA256 hash
and its iteration count, and a check compares in constant time. The store
is an interface (:class:`UserStore`) with two implementations, a JSON
file (the hub's) and memory (tests, embedding), so nothing that checks a
login knows or cares where users live.

The demo account is an ordinary user the hub adds at start, from the
settings, when the demo is on; it is marked ``demo`` so the login page
can say so, and it is removed again when the demo is off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol

__all__ = ["User", "UserStore", "MemoryUserStore", "JsonUserStore", "hash_password", "check_password",
           "sync_demo", "valid_name"]

_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def hash_password(password: str, iterations: int, salt: Optional[bytes] = None) -> str:
    """``pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>``."""
    if not password:
        raise ValueError("a password cannot be empty")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


@dataclass(frozen=True)
class User:
    name: str
    password_hash: str
    role: str = "member"
    demo: bool = False


class UserStore(Protocol):
    def get(self, name: str) -> Optional[User]: ...
    def put(self, user: User) -> None: ...
    def delete(self, name: str) -> None: ...
    def all(self) -> List[User]: ...


class MemoryUserStore:
    def __init__(self) -> None:
        self._users: Dict[str, User] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> Optional[User]:
        return self._users.get(name)

    def put(self, user: User) -> None:
        if not valid_name(user.name):
            raise ValueError(f"user name {user.name!r}: letters, digits and . _ @ - only, up to 64")
        with self._lock:
            self._users[user.name] = user

    def delete(self, name: str) -> None:
        with self._lock:
            self._users.pop(name, None)

    def all(self) -> List[User]:
        return sorted(self._users.values(), key=lambda u: u.name)


class JsonUserStore(MemoryUserStore):
    """Users in one JSON file, written whole and atomically, readable by
    its owner only."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for u in data.get("users", []):
                super().put(User(**u))

    def put(self, user: User) -> None:
        super().put(user)
        self._save()

    def delete(self, name: str) -> None:
        super().delete(name)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": [asdict(u) for u in self.all()]}, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


def sync_demo(store: UserStore, enabled: bool, name: str, password: str, iterations: int) -> Optional[User]:
    """Make the store's demo account match the settings: present with the
    configured password when enabled, gone when not. A real user who
    happens to share the demo's name is never touched."""
    existing = store.get(name)
    if existing is not None and not existing.demo:
        return None
    if not enabled:
        if existing is not None:
            store.delete(name)
        return None
    if existing is not None and check_password(password, existing.password_hash):
        return existing
    user = User(name=name, password_hash=hash_password(password, iterations), role="member", demo=True)
    store.put(user)
    return user
