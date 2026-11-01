"""Same-origin Cookie authentication and ownership checks for the HTTP boundary."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import config
from storage import auth, cache, repos

COOKIE = "cisp_session"
router = APIRouter(prefix="/api")


def validate_origin_config() -> None:
    origin = urlsplit(config.CISP_PUBLIC_ORIGIN)
    if (origin.scheme not in {"http", "https"} or not origin.hostname
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise ValueError("CISP_PUBLIC_ORIGIN must be one canonical HTTP(S) origin")
    if origin.scheme == "http" and origin.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Non-loopback CISP_PUBLIC_ORIGIN requires HTTPS")


def require_origin(request: Request) -> None:
    if request.headers.get("origin") != config.CISP_PUBLIC_ORIGIN:
        raise HTTPException(403, "Invalid request origin")


def require_user(request: Request) -> dict:
    user = auth.current_user(request.cookies.get(COOKIE, ""))
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


USER_DEPENDENCY = Depends(require_user)


def require_csrf(request: Request, user: dict = USER_DEPENDENCY) -> dict:
    require_origin(request)
    expected = auth.csrf_token(request.cookies[COOKIE])
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("ascii")):
        raise HTTPException(403, "Invalid CSRF token")
    return user


WRITE_DEPENDENCY = Depends(require_csrf)


def require_thread(thread_id: str, user: dict) -> None:
    if repos.get_thread_owner(thread_id) != user["id"]:
        raise HTTPException(404, "Thread not found")


def require_diagnostic(session_id: str, user: dict) -> None:
    if repos.get_diagnostic_owner(session_id) != user["id"]:
        raise HTTPException(404, "Diagnostic session not found")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)


def _auth_limit(request: Request, username: str) -> None:
    # Bound all attempts before expensive scrypt; do not trust forwarded IP headers.
    ip = request.client.host if request.client else "unknown"
    normalized = username.strip().lower()
    for scope, value, limit in (("ip", ip, 30), ("username", normalized, 10)):
        key = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if not cache.rate_limit(f"rl:auth:{scope}:{key}", limit, 300)[0]:
            raise HTTPException(429, "Too many authentication attempts")


@router.post("/auth/register", dependencies=[Depends(require_origin)], status_code=201)
def register(req: Credentials, request: Request):
    _auth_limit(request, req.username)
    try:
        return auth.register(req.username, req.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/auth/login", dependencies=[Depends(require_origin)])
def login(req: Credentials, request: Request, response: Response):
    _auth_limit(request, req.username)
    result = auth.login(req.username, req.password)
    if result is None:
        raise HTTPException(401, "Invalid credentials")
    # Replace any existing browser session rather than leaving a live superseded token.
    auth.logout(request.cookies.get(COOKIE, ""))
    response.set_cookie(COOKIE, result["session_token"], max_age=86400,
                        httponly=True, secure=config.CISP_PUBLIC_ORIGIN.startswith("https://"),
                        samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"user": result["user"], "csrf_token": result["csrf_token"]}


@router.get("/auth/me")
def me(request: Request, response: Response, user: dict = USER_DEPENDENCY):
    response.headers["Cache-Control"] = "no-store"
    return {"user": user, "csrf_token": auth.csrf_token(request.cookies[COOKIE])}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: dict = WRITE_DEPENDENCY):
    auth.logout(request.cookies[COOKIE])
    response.delete_cookie(COOKIE, path="/", httponly=True,
                           secure=config.CISP_PUBLIC_ORIGIN.startswith("https://"), samesite="strict")


@router.post("/threads", status_code=201)
def create_thread(user: dict = WRITE_DEPENDENCY):
    if not cache.rate_limit(f"rl:threads:user:{user['id']}", 20, 60)[0]:
        raise HTTPException(429, "Too many new threads")
    return repos.create_thread(user["id"])
