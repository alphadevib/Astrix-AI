"""Account and session service.

Sessions are opaque random bearer tokens. Only the SHA-256 of a token is
persisted, so the session table is useless to anyone who reads it — the same
reasoning as the password column, applied to the credential that replaces it.

A session is revoked by deleting its row (sign-out), by every row for the user
being deleted (password change, "sign out everywhere"), or by passing its
expiry. There is no stateless JWT here on purpose: an operator revoking access
to a mission console expects it to take effect on the next request, not at the
end of a token lifetime.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..memory.db import SessionRow, UserRow
from .passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)

log = logging.getLogger(__name__)

TOKEN_BYTES = 32


class AuthError(Exception):
    """Rejected credential or request. `code` maps to an HTTP status in the router."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive(moment: datetime) -> datetime:
    """SQLite columns are naive; normalise before comparing or storing."""
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


def normalise_email(email: str) -> str:
    return email.strip().lower()


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as routes see it. Carries no secret."""

    id: str
    email: str
    name: str
    role: str
    organisation: str
    session_id: str

    @property
    def operator(self) -> str:
        return self.name or self.email


class LoginThrottle:
    """In-process lockout for repeated failures against one account or address.

    Deliberately simple: a single-process console does not need a shared store,
    and the alternative — no throttle at all — makes an online guessing attack
    against a 10-character password practical.
    """

    def __init__(self, limit: int = 8, window: float = 900.0, lockout: float = 900.0) -> None:
        self.limit = limit
        self.window = window
        self.lockout = lockout
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            until = self._locked.get(key)
            if until and until > now:
                wait = int((until - now) / 60) + 1
                raise AuthError(
                    f"Too many failed attempts. Try again in about {wait} minute(s).", 429
                )
            if until:
                self._locked.pop(key, None)

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._attempts.get(key, []) if now - t < self.window]
            hits.append(now)
            self._attempts[key] = hits
            if len(hits) >= self.limit:
                self._locked[key] = now + self.lockout
                self._attempts.pop(key, None)
                log.warning("auth: locked out %s after %d failed attempts", key, len(hits))

    def succeed(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._locked.pop(key, None)


class AuthService:
    def __init__(self, session_factory, session_ttl_hours: int = 720) -> None:
        self.session_factory = session_factory
        self.ttl = timedelta(hours=max(1, session_ttl_hours))
        self.throttle = LoginThrottle()

    # -- accounts ----------------------------------------------------------- #

    def register(
        self,
        email: str,
        password: str,
        name: str = "",
        organisation: str = "",
        role: str = "flight-director",
    ) -> tuple[Principal, str]:
        email = normalise_email(email)
        name = name.strip()
        if "@" not in email or len(email) < 6:
            raise AuthError("Enter a valid email address.", 422)
        reason = validate_password(password, email=email, name=name)
        if reason:
            raise AuthError(reason, 422)

        user = UserRow(
            id=str(uuid.uuid4()),
            email=email,
            name=name or email.split("@")[0],
            organisation=organisation.strip()[:160],
            role=(role or "flight-director").strip()[:64],
            password_hash=hash_password(password),
            preferences={},
            created_at=_naive(_utcnow()),
            password_changed_at=_naive(_utcnow()),
        )
        with self.session_factory() as db:
            db.add(user)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise AuthError("An account with that email already exists.", 409) from None
            db.refresh(user)
            return self._open_session(db, user)

    def login(self, email: str, password: str, user_agent: str = "") -> tuple[Principal, str]:
        email = normalise_email(email)
        self.throttle.check(email)
        with self.session_factory() as db:
            user = db.scalar(select(UserRow).where(UserRow.email == email))
            # Same message and comparable timing whether or not the account exists.
            if user is None:
                dummy_verify()
                self.throttle.fail(email)
                raise AuthError("Email or password is incorrect.", 401)
            if not verify_password(password, user.password_hash):
                self.throttle.fail(email)
                raise AuthError("Email or password is incorrect.", 401)
            if not user.is_active:
                raise AuthError("This account has been disabled.", 403)

            self.throttle.succeed(email)
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
            user.last_login_at = _naive(_utcnow())
            db.commit()
            return self._open_session(db, user, user_agent)

    def update_profile(self, user_id: str, **fields: Any) -> Principal:
        allowed = {"name", "organisation", "role", "preferences"}
        with self.session_factory() as db:
            user = db.get(UserRow, user_id)
            if user is None:
                raise AuthError("Account not found.", 404)
            for key, value in fields.items():
                if key not in allowed or value is None:
                    continue
                if key == "preferences":
                    user.preferences = {**(user.preferences or {}), **value}
                else:
                    setattr(user, key, str(value).strip()[:160])
            db.commit()
            db.refresh(user)
            return self._principal(user, "")

    def change_password(
        self, user_id: str, current: str, replacement: str, keep_session: str | None = None
    ) -> None:
        with self.session_factory() as db:
            user = db.get(UserRow, user_id)
            if user is None:
                raise AuthError("Account not found.", 404)
            if not verify_password(current, user.password_hash):
                raise AuthError("Current password is incorrect.", 401)
            reason = validate_password(replacement, email=user.email, name=user.name)
            if reason:
                raise AuthError(reason, 422)
            if verify_password(replacement, user.password_hash):
                raise AuthError("Choose a password you have not used here before.", 422)

            user.password_hash = hash_password(replacement)
            user.password_changed_at = _naive(_utcnow())
            # Changing a password ends every other sign-in, which is the whole
            # point of changing it after a suspected compromise.
            statement = delete(SessionRow).where(SessionRow.user_id == user_id)
            if keep_session:
                statement = statement.where(SessionRow.id != keep_session)
            db.execute(statement)
            db.commit()

    # -- sessions ----------------------------------------------------------- #

    def _open_session(self, db, user: UserRow, user_agent: str = "") -> tuple[Principal, str]:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        row = SessionRow(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=token_fingerprint(token),
            user_agent=user_agent[:256],
            created_at=_naive(_utcnow()),
            last_seen_at=_naive(_utcnow()),
            expires_at=_naive(_utcnow() + self.ttl),
        )
        db.add(row)
        db.commit()
        return self._principal(user, row.id), token

    def resolve(self, token: str) -> Principal | None:
        """Principal behind a bearer token, or None if it is unknown or expired."""
        if not token:
            return None
        with self.session_factory() as db:
            row = db.scalar(
                select(SessionRow).where(SessionRow.token_hash == token_fingerprint(token))
            )
            if row is None:
                return None
            if _naive(row.expires_at) < _naive(_utcnow()):
                db.delete(row)
                db.commit()
                return None
            user = db.get(UserRow, row.user_id)
            if user is None or not user.is_active:
                return None
            # Sliding expiry: an operator working a long shift is not signed out
            # mid-mission, while an abandoned token still ages out.
            row.last_seen_at = _naive(_utcnow())
            row.expires_at = _naive(_utcnow() + self.ttl)
            db.commit()
            return self._principal(user, row.id)

    def logout(self, session_id: str) -> None:
        with self.session_factory() as db:
            db.execute(delete(SessionRow).where(SessionRow.id == session_id))
            db.commit()

    def logout_everywhere(self, user_id: str) -> int:
        with self.session_factory() as db:
            result = db.execute(delete(SessionRow).where(SessionRow.user_id == user_id))
            db.commit()
            return result.rowcount or 0

    def sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as db:
            rows = db.scalars(
                select(SessionRow)
                .where(SessionRow.user_id == user_id)
                .order_by(SessionRow.last_seen_at.desc())
            ).all()
            return [
                {
                    "id": r.id,
                    "user_agent": r.user_agent,
                    "created_at": r.created_at.isoformat(),
                    "last_seen_at": r.last_seen_at.isoformat(),
                    "expires_at": r.expires_at.isoformat(),
                }
                for r in rows
            ]

    def purge_expired(self) -> int:
        with self.session_factory() as db:
            result = db.execute(delete(SessionRow).where(SessionRow.expires_at < _naive(_utcnow())))
            db.commit()
            return result.rowcount or 0

    def count_users(self) -> int:
        with self.session_factory() as db:
            return len(db.scalars(select(UserRow.id)).all())

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _principal(user: UserRow, session_id: str) -> Principal:
        return Principal(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            organisation=user.organisation,
            session_id=session_id,
        )

    def profile(self, user_id: str) -> dict[str, Any]:
        with self.session_factory() as db:
            user = db.get(UserRow, user_id)
            if user is None:
                raise AuthError("Account not found.", 404)
            return {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "organisation": user.organisation,
                "role": user.role,
                "preferences": user.preferences or {},
                "created_at": user.created_at.isoformat(),
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            }
