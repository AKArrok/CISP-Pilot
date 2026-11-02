"""Isolated CISP HTTP load test; the server must use synthetic data and fake nodes."""
import json
import os
import uuid
from pathlib import Path

from locust import HttpUser, between, task


class Learner(HttpUser):
    wait_time = between(float(os.getenv("CISP_LOAD_WAIT_MIN", "1")),
                        float(os.getenv("CISP_LOAD_WAIT_MAX", "2")))

    def on_start(self):
        pool = os.getenv("CISP_LOAD_SESSIONS")
        if pool:
            if not hasattr(self.environment, "cisp_sessions"):
                self.environment.cisp_sessions = json.loads(Path(pool).read_text(encoding="utf-8"))
            session = self.environment.cisp_sessions.pop()
            self.client.cookies.set("cisp_session", session["session_token"])
            self.headers = {"Origin": self.host, "X-CSRF-Token": session["csrf_token"]}
            self.thread_id = session["thread_id"]
            return
        credentials = {"username": "load-" + uuid.uuid4().hex, "password": uuid.uuid4().hex}
        headers = {"Origin": self.host}
        r = self.client.post("/api/auth/register", json=credentials, headers=headers, name="setup/register")
        r.raise_for_status()
        r = self.client.post("/api/auth/login", json=credentials, headers=headers, name="setup/login")
        r.raise_for_status()
        self.headers = {**headers, "X-CSRF-Token": r.json()["csrf_token"]}
        r = self.client.post("/api/threads", headers=self.headers, name="setup/thread")
        r.raise_for_status()
        self.thread_id = r.json()["id"]

    @task(3)
    def chat(self):
        with self.client.post("/chat/stream", json={"query": "合成测试：你好", "thread_id": self.thread_id},
                              headers=self.headers, name="chat/SSE", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            except ValueError:
                response.failure("invalid SSE JSON")
                return
            if not any(e.get("type") == "done" for e in events) or any(e.get("type") == "error" for e in events):
                response.failure("SSE missing done or contains error")

    @task(1)
    def quiz(self):
        with self.client.post("/quiz/next", json={"mode": "random", "thread_id": self.thread_id},
                              headers=self.headers, name="quiz/next", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            question_id = response.json().get("id")
            if not question_id:
                response.failure("missing question id")
                return
        with self.client.post("/quiz/answer", json={"question_id": question_id, "choice": "A",
                              "thread_id": self.thread_id, "submission_id": uuid.uuid4().hex},
                              headers=self.headers, name="quiz/answer", catch_response=True) as response:
            if response.status_code != 200 or "correct" not in response.json():
                response.failure(f"invalid quiz answer HTTP {response.status_code}")
