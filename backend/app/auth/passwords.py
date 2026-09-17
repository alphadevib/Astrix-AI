"""Password hashing.

PBKDF2-HMAC-SHA256 from the standard library: no build step, no native wheel, and
it is the algorithm NIST SP 800-63B still lists for verifier storage. Every hash
carries its own random salt and its iteration count, so raising the cost later
does not invalidate existing accounts — `needs_rehash` reports the ones to
upgrade on their next successful sign-in.

Nothing here ever sees a plaintext password twice: `hash_password` is called on
registration and password change, `verify_password` on sign-in, and neither
logs, returns or stores the input.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import b64decode, b64encode

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000  # OWASP's 2023+ floor for PBKDF2-HMAC-SHA256
SALT_BYTES = 16
MIN_LENGTH = 10
MAX_LENGTH = 200  # a hash of an unbounded input is a free denial of service

# Rejected outright. Everything else is left to the user: length beats a
# character-class checklist, and a blocklist of the handful of passwords that
# actually get sprayed catches more than any composition rule.
COMMON = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "12345678", "123456789",
        "1234567890", "qwertyuiop", "letmein123", "iloveyou1", "admin12345",
        "welcome123", "astrix1234", "changeme123", "spacecraft", "mission123",
    }
)


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, iterations)
    return f"{ALGORITHM}${iterations}${b64encode(salt).decode()}${b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a candidate password against a stored hash."""
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        expected = b64decode(digest_b64)
        candidate = _derive(password, b64decode(salt_b64), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, iterations: int = ITERATIONS) -> bool:
    try:
        algorithm, stored, _, _ = encoded.split("$", 3)
    except ValueError:
        return True
    return algorithm != ALGORITHM or int(stored) < iterations


def validate_password(password: str, *, email: str = "", name: str = "") -> str | None:
    """Return a human-readable reason to reject, or None if the password is fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    lowered = password.lower()
    if lowered in COMMON:
        return "That password appears in public breach lists. Choose another."
    if len(set(password)) < 5:
        return "Too repetitive — use a longer mix of characters."
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in lowered:
        return "Don't reuse your email address in your password."
    for part in name.lower().split():
        if len(part) >= 4 and part in lowered:
            return "Don't reuse your name in your password."
    return None


def dummy_verify() -> None:
    """Burn one PBKDF2 round for an unknown email.

    Without it, a sign-in attempt for an account that does not exist returns far
    faster than one that does, which turns the login endpoint into an account
    enumeration oracle.
    """
    _derive("astrix-timing-equaliser", b"\x00" * SALT_BYTES, ITERATIONS)
