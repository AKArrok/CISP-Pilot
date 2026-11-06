"""Run with the existing locust-env Python; identify children by Popen PID."""
import csv
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

import psutil


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
PRIVATE = ROOT / "data/review_packets/auth-profile-20261106-r5"
PYTHON = ROOT / ".venv-win/Scripts/python.exe"


if __name__ == "__main__":
    if (OUTPUT / "summary.json").exists():
        raise ValueError("Never overwrite a previous profile run")
    plan = json.loads((OUTPUT / "plan.json").read_text(encoding="utf-8"))
    before = hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest()
    assert before == plan["default_db_before"]
    for name, expected in plan["frozen_sha256"].items():
        assert hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest() == expected
    for name, expected in plan["runtime_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1", DATABASE_URL="", MYSQL_PASSWORD="",
        CISP_DB_PATH=str(PRIVATE / "auth.db"), CISP_PUBLIC_ORIGIN=origin,
        REDIS_URL="redis://127.0.0.1:1/0", LLM_API_KEY="", DASHSCOPE_API_KEY="", ARK_API_KEY="",
        EMBEDDING_BACKEND="dashscope", CISP_AUTH_PROFILE_ACCOUNTS=str(PRIVATE / "accounts-private.json"))
    with (OUTPUT / "migration.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    samples, stages, negatives, server, load, process = [], [], [], None, None, None
    runtime_pid_verified = False
    runtime_created = None

    def invalid_login(username, label):
        request = urllib.request.Request(origin + "/api/auth/login", method="POST",
            data=json.dumps({"username": username, "password": "incorrect-profile-password"}).encode(),
            headers={"Origin": origin, "Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with opener.open(request, timeout=30) as response:
                response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.read()
            exc.close()
        negatives.append({"kind": label, "status": status, "seconds": time.perf_counter() - started,
                          "started_monotonic": started})
        assert status == 401
    try:
        with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([str(PYTHON), str(OUTPUT / "server-fixture.py"), str(port),
                str(OUTPUT / "scrypt.jsonl")], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 90
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Auth profile server exited during startup")
                try:
                    with opener.open(origin + "/api/health", timeout=1) as response:
                        assert response.status == 200
                        response.read()
                    break
                except OSError as exc:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Auth profile server startup timed out") from exc
                    time.sleep(.2)
            runtime = json.loads((OUTPUT / "runtime.json").read_text(encoding="utf-8"))
            process = psutil.Process(runtime["pid"])
            assert process.pid == server.pid or server.pid in {p.pid for p in process.parents()}
            runtime_pid_verified = True
            runtime_created = process.create_time()
            (OUTPUT / "http-ready.json").write_text(json.dumps({"time_monotonic": time.perf_counter()}),
                                                      encoding="utf-8")
            invalid_login("profile-user-0", "known-wrong-password-after-startup-probe")
            for users in plan["stages_users"]:
                prefix = OUTPUT / f"stage-{users}"
                with prefix.with_suffix(".log").open("w", encoding="utf-8") as load_log:
                    load = subprocess.Popen([sys.executable, "-m", "locust", "-f", str(OUTPUT / "locustfile.py"),
                        "--headless", "--users", str(users), "--spawn-rate", "100", "--run-time", "12s",
                        "--stop-timeout", "10", "--host", origin, "--csv", str(prefix),
                        "--only-summary", "--exit-code-on-error", "1"], cwd=ROOT, env=env,
                        stdout=load_log, stderr=subprocess.STDOUT)
                    while load.poll() is None:
                        cpu = process.cpu_times()
                        started = time.perf_counter()
                        with opener.open(origin + "/api/health", timeout=10) as response:
                            status = response.status
                            response.read()
                        samples.append({"users": users, "time_monotonic": time.perf_counter(),
                            "health_started_monotonic": started,
                            "server_pid": process.pid, "rss_bytes": process.memory_info().rss,
                            "cpu_user_seconds": cpu.user, "cpu_system_seconds": cpu.system,
                            "health_elapsed_seconds": time.perf_counter() - started, "health_status": status})
                        time.sleep(.2)
                    if load.returncode != 0:
                        raise RuntimeError(f"Locust stage{users} failed; preserve logs")
                with Path(str(prefix) + "_stats.csv").open(encoding="utf-8", newline="") as stream:
                    stats = list(csv.DictReader(stream))
                login = next(row for row in stats if row["Name"] == "auth/login")
                stages.append({"users": users, "login_count": int(login["Request Count"]),
                    "login_failures": int(login["Failure Count"]), "stats": stats})
                assert int(login["Request Count"]) == users * 2
                assert all(int(row["Failure Count"]) == 0 for row in stats)
            invalid_login("nonexistent-profile-user", "unknown-account-warm-cache")
    finally:
        if runtime_pid_verified and process.is_running() and process.create_time() == runtime_created:
            process.terminate()
            psutil.wait_procs([process], timeout=15)
        for child in (load, server):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
        after = hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest()
        for name, value in {"resources.json": samples, "summary.json": {
            "stages": stages, "negative_logins": negatives,
            "default_db_before": before, "default_db_after": after,
            "default_db_unchanged": before == after, "model_calls": 0,
            "server_pid": server.pid if server else None,
            "runtime_pid": process.pid if process else None,
            "resource_pid_verified": runtime_pid_verified,
            "runtime_stopped": process is None or not process.is_running(),
            "server_stopped": server is None or server.poll() is not None}}.items():
            (OUTPUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    assert before == after and len(stages) == 3
    print(json.dumps({"login_requests": sum(x["login_count"] for x in stages), "samples": len(samples)}))
