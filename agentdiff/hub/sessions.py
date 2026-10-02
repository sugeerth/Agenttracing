"""Signed-in sessions, the token that guards every form, and a login throttle.

A session is a random token in an ``HttpOnly; SameSite=Strict`` cookie,
mapped here to a user and an expiry. Each session has its own form token
(CSRF): a form that changes state carries it, and the hub refuses a post
without it. Before signing in there is no session, so the login form uses
a short-lived pre-login token kept the same way.

Sessions live in memory: restarting the hub signs everyone out, which is
the honest behaviour for a tool that keeps no database.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

__all__ = ["Session", "SessionStore", "LoginThrottle"]


@dataclass
class Session:
    token: str
    user: str
    csrf: str
    expires: float


class SessionStore:
    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.time) -> None:
        self.ttl_s = ttl_s
        self._clock = clock
        self._sessions: Dict[str, Session] = {}
        self._prelogin: Dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self, user: str) -> Session:
        s = Session(secrets.token_urlsafe(32), user, secrets.token_urlsafe(24), self._clock() + self.ttl_s)
        with self._lock:
            self._sessions[s.token] = s
        return s

    def get(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if s and s.expires < self._clock():
                del self._sessions[token]
                return None
            return s

    def end(self, token: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def end_user(self, user: str) -> None:
        with self._lock:
            for t in [t for t, s in self._sessions.items() if s.user == user]:
                del self._sessions[t]

    def count(self, user: str) -> int:
        now = self._clock()
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.user == user and s.expires >= now)

    @staticmethod
    def csrf_ok(session: Optional[Session], given: Optional[str]) -> bool:
        return bool(session and given and hmac.compare_digest(session.csrf, given))

    # the login form's token, before there is a session
    def prelogin(self) -> str:
        token = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._prelogin = {t: e for t, e in self._prelogin.items() if e > now}
            self._prelogin[token] = now + 900
        return token

    def take_prelogin(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            expiry = self._prelogin.pop(token, None)
        return expiry is not None and expiry > self._clock()


class LoginThrottle:
    """After ``attempts`` failures for one name, refuse it for ``lockout_s``.
    A success clears the count."""

    def __init__(self, attempts: int, lockout_s: float, clock: Callable[[], float] = time.time) -> None:
        self.attempts, self.lockout_s, self._clock = attempts, lockout_s, clock
        self._failures: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def wait_s(self, name: str) -> float:
        with self._lock:
            count, since = self._failures.get(name, (0, 0.0))
        if count < self.attempts:
            return 0.0
        return max(0.0, since + self.lockout_s - self._clock())

    def failed(self, name: str) -> None:
        with self._lock:
            count, _ = self._failures.get(name, (0, 0.0))
            self._failures[name] = (count + 1, self._clock())

    def succeeded(self, name: str) -> None:
        with self._lock:
            self._failures.pop(name, None)
