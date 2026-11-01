"""Password and session services; HTTP ownership/CSRF enforcement is a separate layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import threading

from sqlalchemy.exc import IntegrityError

from storage import repos


# OWASP's scrypt baseline; maxmem includes OpenSSL's allocation overhead.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**17, 8, 1
_MAXMEM = 256 * 1024 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_PASSWORD_WORK = threading.BoundedSemaphore(2)


def _derive(password: str, salt: bytes) -> bytes:
    # Each scrypt job uses about 128 MiB. Bound aggregate work per process.
    with _PASSWORD_WORK:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            maxmem=_MAXMEM, dklen=32,
        )


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12 to 128 characters")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not 1 <= len(password) <= 128:
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        # Accept only our supported format, never attacker-controlled work factors.
        if (algorithm, n, r, p) != ("scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P)):
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    actual = _derive(password, salt)
    return hmac.compare_digest(actual, expected)


def _username(username: str) -> str:
    value = username.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value) is None:
        raise ValueError("Username must contain 3 to 64 ASCII letters, digits, dots, dashes or underscores")
    return value


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"]}


def register(username: str, password: str) -> dict:
    username = _username(username)
    password_hash = hash_password(password)
    try:
        return _public_user(repos.create_user(username, password_hash))
    except IntegrityError as exc:
        raise ValueError("Username unavailable") from exc


def csrf_token(session_token: str) -> str:
    return hmac.new(session_token.encode("ascii"), b"cisp-csrf-v1", hashlib.sha256).hexdigest()


def login(username: str, password: str) -> dict | None:
    try:
        username = _username(username)
    except ValueError:
        return None
    if not 1 <= len(password) <= 128:
        return None
    user = repos.get_user_by_username(username)
    if user is None:
        # Keep unknown accounts on the same expensive password-verification path.
        _derive(password, bytes(16))
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)
    repos.create_web_session(user["id"], hashlib.sha256(token.encode("ascii")).hexdigest(), expires_at)
    return {"user": _public_user(user), "session_token": token,
            "csrf_token": csrf_token(token), "expires_at": expires_at}


def current_user(session_token: str) -> dict | None:
    if _TOKEN.fullmatch(session_token) is None:
        return None
    user = repos.get_active_session_user(hashlib.sha256(session_token.encode("ascii")).hexdigest())
    return _public_user(user) if user else None


def logout(session_token: str) -> bool:
    if _TOKEN.fullmatch(session_token) is None:
        return False
    return repos.revoke_web_session(hashlib.sha256(session_token.encode("ascii")).hexdigest())
