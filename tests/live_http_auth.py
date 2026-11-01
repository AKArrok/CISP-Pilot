"""Explicit, real HTTP identity for manually run live scripts."""

import http.cookiejar
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class LiveAuth:
    def __init__(self):
        base = os.getenv("CISP_TEST_BASE_URL", "").rstrip("/")
        parsed = urlsplit(base)
        if (not base or parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Set CISP_TEST_BASE_URL to the server's canonical origin")
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.csrf = None

    def request(self, method, path, body=None, timeout=300):
        headers = {"Origin": self.base}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
            method=method,
        )
        return self.opener.open(request, timeout=timeout)

    def post_json(self, path, body=None, timeout=300):
        with self.request("POST", path, body, timeout) as response:
            content = response.read()
        return json.loads(content) if content else None

    def get_json(self, path, timeout=60):
        with self.request("GET", path, timeout=timeout) as response:
            return json.loads(response.read())

    def new_thread(self):
        # The service permits 20 new threads per user per minute. Preparation
        # occurs before stress timing, so the concurrent workload stays intact.
        for attempt in range(65):
            try:
                return self.post_json("/api/threads")["id"]
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 64:
                    raise
                time.sleep(1)

    def __enter__(self):
        username = os.getenv("CISP_TEST_USERNAME")
        password = os.getenv("CISP_TEST_PASSWORD")
        if bool(username) != bool(password):
            raise ValueError("Set both CISP_TEST_USERNAME and CISP_TEST_PASSWORD, or neither")
        if not username:
            username = "test-" + secrets.token_hex(12)
            password = secrets.token_urlsafe(24)
            self.post_json("/api/auth/register", {"username": username, "password": password})
        result = self.post_json("/api/auth/login", {"username": username, "password": password})
        self.csrf = result["csrf_token"]
        return self

    def __exit__(self, *_):
        if self.csrf:
            self.post_json("/api/auth/logout")
            self.csrf = None
