"""Frozen local business load with native final stats and actual PID sampling."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import psutil


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent


if __name__ == "__main__":
    if (OUTPUT / "summary.json").exists():
        raise ValueError("Never overwrite a prior business run")
    plan = json.loads((OUTPUT / "plan.json").read_text(encoding="utf-8"))
    for name, expected in plan["frozen_sha256"].items():
        assert hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest() == expected
    for name, expected in plan["runtime_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    default = ROOT / "data/cisp_qa.db"
    before = hashlib.sha256(default.read_bytes()).hexdigest()
    assert before == plan["default_db_before"]
    private = OUTPUT / "operator-private"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
        CISP_DB_PATH=str(private / "demo/cisp_qa.db"), DATABASE_URL="", MYSQL_PASSWORD="",
        CISP_PUBLIC_ORIGIN=origin, REDIS_URL="redis://127.0.0.1:1/0", EMBEDDING_BACKEND="dashscope",
        LLM_API_KEY="", LLM_BASE_URL="http://127.0.0.1:1/v1", DASHSCOPE_API_KEY="", ARK_API_KEY="",
        CISP_LOAD_SESSIONS=str(private / "sessions-private.json"), CISP_LOAD_RUNTIME=str(OUTPUT / "spawn.json"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    server, load, process = None, None, None
    samples, stats = [], []
    verified = False
    try:
        with (OUTPUT / "server.log").open("w", encoding="utf-8") as server_log:
            server = subprocess.Popen([str(ROOT / ".venv-win/Scripts/python.exe"),
                str(OUTPUT / "server-fixture.py"), str(port)], cwd=ROOT, env=env,
                stdout=server_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Business fixture exited before serving")
                try:
                    with opener.open(origin + "/api/health", timeout=1) as response:
                        response.read()
                        assert response.status == 200
                    break
                except OSError as exc:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Business startup timed out") from exc
                    time.sleep(.2)
            runtime = json.loads((OUTPUT / "setup.json").read_text(encoding="utf-8"))
            process = psutil.Process(runtime["runtime_pid"])
            assert process.pid == server.pid or server.pid in {p.pid for p in process.parents()}
            verified = True
            with (OUTPUT / "locust.log").open("w", encoding="utf-8") as log:
                load = subprocess.Popen([sys.executable, "-m", "locust", "-f", str(OUTPUT / "locustfile.py"),
                    "--headless", "--users", "8", "--spawn-rate", "2", "--run-time", "180s",
                    "--stop-timeout", "10", "--host", origin, "--csv", str(OUTPUT / "business"),
                    "--json-file", str(OUTPUT / "business-final"), "--only-summary",
                    "--exit-code-on-error", "1"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 210
                while load.poll() is None:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Business load exceeded frozen duration and drain")
                    started = time.perf_counter()
                    with opener.open(origin + "/api/health", timeout=5) as response:
                        response.read()
                        status = response.status
                    cpu = process.cpu_times()
                    samples.append({"time_monotonic": time.perf_counter(), "runtime_pid": process.pid,
                        "rss_bytes": process.memory_info().rss, "cpu_user_s": cpu.user,
                        "cpu_system_s": cpu.system, "health_status": status,
                        "health_seconds": time.perf_counter() - started})
                    time.sleep(.2)
                assert load.returncode == 0
            stats = json.loads((OUTPUT / "business-final.json").read_text(encoding="utf-8"))
            counts = {row["name"]: row["num_requests"] for row in stats}
            assert set(counts) == {"chat/SSE", "quiz/next", "quiz/answer"}
            assert all(row["num_requests"] > 0 and row["num_failures"] == 0 for row in stats)
            assert json.loads((OUTPUT / "spawn.json").read_text(encoding="utf-8"))["users"] == 8
    finally:
        for child in (load, server):
            if child is not None and child.poll() is None:
                descendants = psutil.Process(child.pid).children(recursive=True)
                for owned in reversed(descendants):
                    owned.terminate()
                child.terminate()
                psutil.wait_procs(descendants, timeout=15)
                child.wait(timeout=15)
        after = hashlib.sha256(default.read_bytes()).hexdigest()
        (OUTPUT / "resources.json").write_text(json.dumps(samples, indent=2) + "\n", encoding="utf-8")
        (OUTPUT / "summary.json").write_text(json.dumps({"runtime_pid_verified": verified,
            "status": "http_complete_pending_sql_audit" if stats else "stopped_before_http_complete",
            "runtime_pid": process.pid if process else None, "runtime_stopped": process is None or not process.is_running(),
            "default_db_before": before, "default_db_after": after, "default_db_unchanged": before == after,
            "model_calls": 0, "completed_stats": stats}, indent=2) + "\n", encoding="utf-8")
    database = private / "demo/cisp_qa.db"
    with sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True) as connection:
        roles = dict(connection.execute("select role,count(*) from conversations group by role"))
        receipts = connection.execute("select count(*) from answer_submissions").fetchone()[0]
        maximum = connection.execute("select max(length(content)) from conversations").fetchone()[0]
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        assert roles == {"user": counts["chat/SSE"], "assistant": counts["chat/SSE"]}
        assert receipts == counts["quiz/answer"] and maximum < 100 and integrity == "ok"
        database_audit = {"roles": roles, "answer_receipts": receipts, "max_message_chars": maximum,
                          "integrity": integrity, "database_bytes": database.stat().st_size}
    assert before == after and verified and not process.is_running()
    (OUTPUT / "persistence-audit.json").write_text(json.dumps(database_audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"business_requests": sum(counts.values()), "failures": 0, "database": database_audit}))
