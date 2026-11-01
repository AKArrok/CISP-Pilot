"""Generate fresh synthetic data at a new path and smoke an explicit Docker image.

Retains bind-mounted SQLite and evidence; removes only the container it creates.
This sparse, offline check does not establish dense retrieval or LLM quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_demo import prepare  # noqa: E402

OWNER_LABEL = "cisp.engineering.smoke"


def owned_container(name: str, token: str) -> str | None:
    result = subprocess.run(
        ["docker", "inspect", "--format",
         '{"Id":{{json .Id}},"Owner":{{json (index .Config.Labels "' + OWNER_LABEL + '")}}}', name],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        docker("info", "--format", "{{.ServerVersion}}")
        if docker("ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}"):
            raise RuntimeError("Named container exists but ownership inspection failed")
        return None
    found = json.loads(result.stdout)
    identifier = found.get("Id", "")
    if found.get("Owner") != token or len(identifier) != 64 or any(
            character not in "0123456789abcdef" for character in identifier):
        raise RuntimeError("Container ownership could not be verified")
    return identifier


def overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def database_evidence(database: Path) -> tuple[dict, list]:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("users", "learner_attempts")}
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
    return counts, integrity


def docker(*arguments: str, timeout: int = 30) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        # Do not include command arguments or environments in error evidence.
        raise RuntimeError(f"Docker {arguments[0]} failed with exit {result.returncode}")
    return result.stdout.strip()


def request(client: httpx.Client, method: str, path: str, status: int = 200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != status:
        raise RuntimeError(f"{method} {path}: expected {status}, got {response.status_code}")
    return response


def wait_ready(client: httpx.Client, container: str) -> str:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if docker("inspect", "--format", "{{.State.Running}}", container) != "true":
            raise RuntimeError("Container stopped before readiness")
        try:
            health, ready = client.get("/api/health"), client.get("/api/ready")
            if health.status_code == ready.status_code == 200:
                return ready.http_version
        except httpx.TransportError:
            pass
        time.sleep(0.3)
    raise RuntimeError("Health/readiness did not both reach 200")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    data, evidence = args.data_dir.resolve(), args.evidence_dir.resolve()
    container = None
    run_attempted = False
    name, token = f"cisp-smoke-{secrets.token_hex(8)}", secrets.token_hex(32)
    evidence_created = False
    summary = {"ok": False, "stage": "validate", "image": args.image,
               "data_dir": str(data), "evidence_dir": str(evidence),
               "synthetic_demo": True, "human_reviewed": False,
               "retrieval_mode": "sparse", "model_quality_verified": False,
               "default_dense_configuration_verified": False}
    try:
        protected = (ROOT / "data").resolve()
        if overlaps(data, protected) or overlaps(evidence, protected):
            raise ValueError("Paths must not overlap the default data directory")
        if data.exists() or overlaps(data, evidence):
            raise ValueError("Data must be a new path and evidence must be separate")
        evidence.mkdir(parents=True, exist_ok=False)
        evidence_created = True
        summary["stage"] = "prepare_synthetic_data"
        prepare(data)
        database = data / "cisp_qa.db"
        counts, integrity = database_evidence(database)
        if counts != {"users": 0, "learner_attempts": 0} or integrity != [("ok",)]:
            raise ValueError("Synthetic database must have no users or attempts and pass integrity")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        environment = {
            "PYTHON_DOTENV_DISABLED": "1", "DATABASE_URL": "", "MYSQL_PASSWORD": "",
            "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "1", "LLM_API_KEY": "",
            "DASHSCOPE_API_KEY": "", "ARK_API_KEY": "", "ARK_EMBEDDING_API_KEY": "",
            "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "DEEPSEEK_API_KEY": "",
            "LLM_BASE_URL": "http://127.0.0.1:1/v1", "EMBEDDING_BACKEND": "dashscope",
            "ENABLE_DENSE_RETRIEVAL": "false", "ENABLE_RERANKING": "false",
            "ENABLE_QUERY_REWRITE": "false", "ENABLE_QUERY_VARIANTS": "false",
            "REDIS_URL": "redis://127.0.0.1:1/0", "CISP_PUBLIC_ORIGIN": origin,
            "CISP_DB_PATH": "/app/data/cisp_qa.db",
            "CISP_COURSE_DIR": "/nonexistent/cisp-smoke-source",
            "CISP_EXAM_DIR": "/nonexistent/cisp-smoke-source",
        }
        summary.update(container_name=name, ownership_label=OWNER_LABEL, ownership_token=token)
        (evidence / "ownership.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        command = ["run", "--detach", "--name", name,
                   "--label", f"{OWNER_LABEL}={token}", "--cidfile", str(evidence / "container-id.txt"),
                   "--publish", f"127.0.0.1:{port}:9528", "--mount",
                   f"type=bind,source={data},target=/app/data"]
        for key, value in environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.append(args.image)
        summary["stage"] = "docker_run"
        run_attempted = True
        created = docker(*command, timeout=60)
        if len(created) != 64 or any(character not in "0123456789abcdef" for character in created):
            raise RuntimeError("Docker run did not return a container ID")
        container = created
        summary.update(container_id=container, origin=origin)
        with httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False,
                          timeout=10) as owner, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False,
                             timeout=10) as other:
            summary["stage"] = "first_start"
            summary["http_version"] = wait_ready(owner, container)
            threads = []
            for number, client in enumerate((owner, other)):
                credentials = {"username": f"docker-smoke-{number}-{secrets.token_hex(6)}",
                               "password": secrets.token_urlsafe(24)}
                request(client, "POST", "/api/auth/register", 201, json=credentials)
                login = request(client, "POST", "/api/auth/login", json=credentials).json()
                client.headers["X-CSRF-Token"] = login["csrf_token"]
                threads.append(request(client, "POST", "/api/threads", 201).json()["id"])
            summary["stage"] = "answer_replay_and_ownership"
            payload = {"question_id": "demo-q-01-1", "choice": "A", "thread_id": threads[0],
                       "submission_id": "docker-smoke-answer-1"}
            receipt = request(owner, "POST", "/quiz/answer", json=payload).json()
            if request(owner, "POST", "/quiz/answer", json=payload).json() != receipt:
                raise RuntimeError("Same-payload replay changed receipt")
            request(owner, "POST", "/quiz/answer", 409, json={**payload, "choice": "B"})
            request(other, "GET", "/api/stats", 404, params={"thread_id": threads[0]})
            request(other, "POST", "/quiz/answer", 404, json=payload)
            request(other, "GET", "/api/stats", params={"thread_id": threads[1]})
            summary["stage"] = "restart_persistence"
            docker("restart", container, timeout=60)
            wait_ready(owner, container)
            if request(owner, "POST", "/quiz/answer", json=payload).json() != receipt:
                raise RuntimeError("Original session replay changed receipt after restart")
        summary["stage"] = "sqlite_evidence"
        counts, integrity = database_evidence(database)
        if counts != {"users": 2, "learner_attempts": 1} or integrity != [("ok",)]:
            raise RuntimeError("Synthetic database counts/integrity failed")
        summary.update(ok=True, stage="complete", counts=counts, integrity="ok",
                       container_restart_verified=True, docker_image_verified=True,
                       receipt_sha256=hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest())
    except Exception as exc:
        summary.update(error_type=type(exc).__name__, error=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Operation failed")
    finally:
        if run_attempted:
            try:
                container = owned_container(name, token)
                summary["cleanup_pending"] = container is None
                if container is None:
                    # A timed-out create request can arrive after this empty snapshot.
                    summary.update(ok=False, cleanup_error="Owned container not confirmed; retain ownership for later check")
            except Exception as exc:
                container = None
                summary.update(ok=False, cleanup_pending=True, cleanup_error_type=type(exc).__name__)
        if container is not None:
            # Only selected inspect fields are retained; Config.Env is never exported.
            for filename, arguments in (
                ("docker.log", ("logs", container)),
                ("inspect.json", ("inspect", "--format",
                 '{"Id":{{json .Id}},"Image":{{json .Image}},"State":{{json .State}},'
                 '"Mounts":{{json .Mounts}},"Ports":{{json .NetworkSettings.Ports}}}', container)),
            ):
                try:
                    result = subprocess.run(["docker", *arguments], capture_output=True, timeout=30)
                    if result.returncode:
                        raise RuntimeError("Docker evidence collection failed")
                    (evidence / filename).write_bytes(result.stdout + (result.stderr if filename == "docker.log" else b""))
                except Exception as exc:
                    summary.update(ok=False, evidence_error_type=type(exc).__name__)
            try:
                docker("rm", "--force", container)
                summary["container_removed"] = True
            except Exception as exc:
                summary.update(ok=False, cleanup_pending=True, cleanup_error_type=type(exc).__name__, container_removed=False)
        if evidence_created:
            try:
                (evidence / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                summary.update(ok=False, summary_error_type=type(exc).__name__)
        print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
