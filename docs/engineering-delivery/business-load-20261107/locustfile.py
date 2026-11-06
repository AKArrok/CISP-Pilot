"""The original bounded business mix with mandatory preauthenticated sessions."""
import json
import os
from pathlib import Path
import time
import uuid

from locust import HttpUser, between, events, task


@events.spawning_complete.add_listener
def record_spawn(user_count, **kwargs):
    Path(os.environ["CISP_LOAD_RUNTIME"]).write_text(json.dumps({"users": user_count,
        "time_monotonic": time.perf_counter()}), encoding="utf-8")


class Learner(HttpUser):
    wait_time = between(2, 3)

    def on_start(self):
        if not hasattr(self.environment, "cisp_sessions"):
            self.environment.cisp_sessions = json.loads(
                Path(os.environ["CISP_LOAD_SESSIONS"]).read_text(encoding="utf-8"))
        session = self.environment.cisp_sessions.pop()
        self.client.cookies.set("cisp_session", session["session_token"])
        self.headers = {"Origin": self.host, "X-CSRF-Token": session["csrf_token"]}
        self.thread_id = session["thread_id"]

    @task(3)
    def chat(self):
        with self.client.post("/chat/stream", json={"query": "合成测试：你好", "thread_id": self.thread_id},
                headers=self.headers, name="chat/SSE", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP{response.status_code}")
                return
            try:
                data = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            except ValueError:
                response.failure("Invalid SSE JSON")
                return
            if not data or data[-1].get("type") != "done" or any(x.get("type") == "error" for x in data):
                response.failure("Missing SSEdone or error event")

    @task(1)
    def quiz(self):
        with self.client.post("/quiz/next", json={"mode": "random", "thread_id": self.thread_id},
                headers=self.headers, name="quiz/next", catch_response=True) as response:
            if response.status_code != 200 or not response.json().get("id"):
                response.failure("Invalid quiz question")
                return
            question = response.json()["id"]
        with self.client.post("/quiz/answer", json={"question_id": question, "choice": "A",
                "thread_id": self.thread_id, "submission_id": uuid.uuid4().hex}, headers=self.headers,
                name="quiz/answer", catch_response=True) as response:
            if response.status_code != 200 or "correct" not in response.json():
                response.failure("Invalid quiz answer")
