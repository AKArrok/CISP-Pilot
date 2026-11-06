"""Two real login/session/revocation cycles per synthetic user, then stop."""
import itertools
import json
import os
from pathlib import Path

from locust import HttpUser, task
from locust.exception import StopUser


ACCOUNTS = json.loads(Path(os.environ["CISP_AUTH_PROFILE_ACCOUNTS"]).read_text(encoding="utf-8"))
COUNTER = itertools.count()


class LoginUser(HttpUser):
    def on_start(self):
        self.account = ACCOUNTS[next(COUNTER)]

    @task
    def login_cycles(self):
        for _ in range(2):
            with self.client.post("/api/auth/login", json=self.account,
                    headers={"Origin": self.host}, name="auth/login", catch_response=True) as response:
                if response.status_code != 200:
                    response.failure(f"Unexpected login HTTP{response.status_code}")
                    raise StopUser()
                value = response.json()
                if value.get("user", {}).get("username") != self.account["username"] or not value.get("csrf_token"):
                    response.failure("Invalid identity or CSRF response")
                    raise StopUser()
                csrf = value["csrf_token"]
            with self.client.get("/api/auth/me", name="auth/me", catch_response=True) as response:
                if response.status_code != 200 or response.json().get("user", {}).get("username") != self.account["username"]:
                    response.failure("Session identity not retained")
                    raise StopUser()
            with self.client.post("/api/auth/logout", headers={"Origin": self.host, "X-CSRF-Token": csrf},
                    name="auth/logout", catch_response=True) as response:
                if response.status_code != 204:
                    response.failure(f"Unexpected logout HTTP{response.status_code}")
            with self.client.get("/api/auth/me", name="auth/revoked", catch_response=True) as response:
                if response.status_code == 401:
                    response.success()
                else:
                    response.failure("Logged-out session still accepted")
        raise StopUser()
