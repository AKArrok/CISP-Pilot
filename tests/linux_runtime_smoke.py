"""Offline Linux runtime smoke CLI; retains synthetic SQLite and logs as evidence.

Run with the installed runtime lock, explicit --source-root and new --work-dir.
This copies Dockerfile's application allowlist; it does not validate a Docker image
or model quality. No chat, question generation, embedding or rerank API is called.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import httpx


FILES = (
    "requirements-build.lock.txt", "requirements-runtime.lock.txt", "config.py", "domains.py", "document_library.py",
    "llms.py", "server.py", "alembic.ini", "scripts/audit_db_readiness.py",
    "scripts/build_taxonomy.py", "scripts/import_to_db.py", "scripts/prepare_demo.py",
)
DIRECTORIES = ("agents", "quiz", "retrieval", "storage", "data_ingest", "alembic", "static")


def copy_application(source: Path, app: Path) -> None:
    paths = [source / name for name in FILES]
    for name in DIRECTORIES:
        directory = source / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Application directory missing or symlinked")
        paths.extend(path for path in directory.rglob("*")
                     if path.suffix in {".py", ".mako", ".html", ".css", ".js"}
                     and not any(part.startswith(".") or part in {"data", "tests", "__pycache__"}
                                 for part in path.relative_to(source).parts))
    for path in paths:
        if (any(parent.is_symlink() for parent in (path, *path.parents) if parent != source)
                or source not in path.resolve().parents):
            raise ValueError("Application copy must stay inside source-root")
        if path.is_file():
            target = app / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        else:
            raise ValueError("Application file missing")


def stop(process: subprocess.Popen | None) -> None:
    if process is not None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def request(client: httpx.Client, method: str, path: str, status: int = 200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != status:
        raise RuntimeError(f"{method} {path.split('?')[0]}: expected {status}, got {response.status_code}")
    return response


def wait_ready(client: httpx.Client, process: subprocess.Popen) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Server exited before readiness")
        try:
            health = client.get("/api/health")
            ready = client.get("/api/ready")
            if health.status_code == ready.status_code == 200:
                return ready.http_version
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Health/readiness did not both reach 200")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    source, work = args.source_root.resolve(), args.work_dir.resolve()
    process, summary = None, {"ok": False, "stage": "validate", "work_dir": str(work)}
    try:
        if sys.platform != "linux":
            raise ValueError("This acceptance CLI requires a real Linux runtime")
        protected = (source / "data").resolve()
        if work == protected or protected in work.parents or work in protected.parents:
            raise ValueError("work-dir must not overlap source-root/data")
        work.mkdir(parents=True, exist_ok=False)
        app = work / "app"
        summary["stage"] = "copy_application"
        copy_application(source, app)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        # A minimal environment prevents inherited provider secrets and DB/proxy config.
        env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
               "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHON_DOTENV_DISABLED": "1", "DATABASE_URL": "", "MYSQL_PASSWORD": "",
               "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "1",
               "REDIS_URL": "redis://127.0.0.1:1/0", "LLM_API_KEY": "",
               "DASHSCOPE_API_KEY": "", "ARK_API_KEY": "", "ARK_EMBEDDING_API_KEY": "",
               "LLM_BASE_URL": "http://127.0.0.1:1/v1", "EMBEDDING_BACKEND": "dashscope",
               "ENABLE_DENSE_RETRIEVAL": "false", "ENABLE_RERANKING": "false",
               "ENABLE_QUERY_REWRITE": "false", "ENABLE_QUERY_VARIANTS": "false",
               "CISP_PUBLIC_ORIGIN": origin}
        summary["stage"] = "prepare_demo"
        with (work / "prepare_demo.log").open("wb") as log:
            subprocess.run([sys.executable, "-B", "scripts/prepare_demo.py", "--output-dir",
                            str(work / "demo")], cwd=app, env=env, stdout=log, stderr=log,
                           check=True, timeout=120)
        shutil.copytree(work / "demo", app / "data")
        env["CISP_DB_PATH"] = str(app / "data" / "cisp_qa.db")
        command = [sys.executable, "-B", "-m", "uvicorn", "server:app", "--host",
                   "127.0.0.1", "--port", str(port)]
        with (work / "server.log").open("ab") as log, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False,
                             timeout=10) as owner, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False,
                             timeout=10) as other:
            summary["stage"] = "first_start"
            process = subprocess.Popen(command, cwd=app, env=env, stdout=log, stderr=log)
            summary["http_version"] = wait_ready(owner, process)
            threads = []
            for number, client in enumerate((owner, other)):
                credentials = {"username": f"smoke-{number}-{secrets.token_hex(6)}",
                               "password": secrets.token_urlsafe(24)}
                request(client, "POST", "/api/auth/register", 201, json=credentials)
                login = request(client, "POST", "/api/auth/login", json=credentials).json()
                client.headers["X-CSRF-Token"] = login["csrf_token"]
                threads.append(request(client, "POST", "/api/threads", 201).json()["id"])
            summary["stage"] = "answer_replay_and_ownership"
            payload = {"question_id": "demo-q-01-1", "choice": "A", "thread_id": threads[0],
                       "submission_id": "linux-smoke-answer-1"}
            receipt = request(owner, "POST", "/quiz/answer", json=payload).json()
            if request(owner, "POST", "/quiz/answer", json=payload).json() != receipt:
                raise RuntimeError("Same-payload replay changed receipt")
            request(owner, "POST", "/quiz/answer", 409, json={**payload, "choice": "B"})
            request(other, "GET", "/api/stats", 404, params={"thread_id": threads[0]})
            request(other, "POST", "/quiz/answer", 404, json=payload)
            request(other, "GET", "/api/stats", params={"thread_id": threads[1]})
            summary["stage"] = "restart_persistence"
            stop(process)
            process = subprocess.Popen(command, cwd=app, env=env, stdout=log, stderr=log)
            wait_ready(owner, process)
            if request(owner, "POST", "/quiz/answer", json=payload).json() != receipt:
                raise RuntimeError("Original session replay changed receipt after restart")
            stop(process)
        summary["stage"] = "sqlite_evidence"
        database = app / "data" / "cisp_qa.db"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("learner_attempts", "users")}
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if counts != {"learner_attempts": 1, "users": 2} or integrity != [("ok",)]:
            raise RuntimeError("Synthetic database counts/integrity failed")
        summary.update(ok=True, stage="complete", counts=counts, integrity="ok",
                       receipt_sha256=hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest(),
                       source_data="not_read_or_copied", synthetic_demo=True,
                       docker_image_verified=False, model_quality_verified=False)
        return 0
    except Exception as exc:
        # Never serialize cookies, credentials, response bodies or process environments.
        summary["error_type"] = type(exc).__name__
        return 1
    finally:
        stop(process)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
