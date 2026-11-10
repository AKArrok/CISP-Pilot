"""Reproducible browser acceptance with temporary auth/storage and restart verification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, help="Optional explicit catalog snapshot for data-platform acceptance")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    protected = [ROOT / "data" / name for name in ("cisp_qa.db", "kb_chunks.json", "manifest.json")]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected if path.exists()}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CISP_EVAL_DEMO_PASSWORD": secrets.token_urlsafe(24),
           "CISP_EVAL_TEST_ORIGIN": origin, "CISP_EVAL_TEST_OUTPUT": str(output), "PYTHONUTF8": "1"}
    if args.knowledge:
        env["CISP_DATA_TEST_SNAPSHOT"] = str(args.knowledge.resolve())
    with tempfile.TemporaryDirectory(prefix="cisp-evaluation-browser-") as temp:
        command = [sys.executable, "scripts/serve_evaluation.py", "--workspace", str(Path(temp) / "demo"), "--port", str(port)]
        with (output / "server.log").open("w", encoding="utf-8") as log:
            process = None

            def stop():
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

            def start():
                child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        if child.poll() is not None:
                            raise RuntimeError("Isolated demo exited; see server.log")
                        try:
                            if httpx.get(origin + "/api/ready", timeout=1).status_code == 200:
                                return child
                        except httpx.HTTPError:
                            pass
                        time.sleep(.2)
                    raise TimeoutError("Isolated demo readiness timeout")
                except BaseException:
                    child.terminate()
                    child.wait(timeout=10)
                    raise

            try:
                process = start()
                subprocess.run([os.getenv("CISP_NODE", "node"), "tests/evaluation_browser.cjs", str(args.baseline.resolve()),
                                str(args.candidate.resolve())], cwd=ROOT, env=env, check=True)
                stop()
                process = start()
                # Full local regression can concurrently saturate scrypt; no provider retry or auth policy change.
                with httpx.Client(base_url=origin, timeout=30) as client:
                    login = client.post("/api/auth/login", headers={"Origin": origin},
                                        json={"username": "eval-reviewer", "password": env["CISP_EVAL_DEMO_PASSWORD"]})
                    login.raise_for_status()
                    headers = {"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]}
                    runs = client.get("/api/evaluation/runs").json()["items"]
                    baseline_name = json.loads(args.baseline.read_text(encoding="utf-8"))["name"]
                    baseline = next(row for row in runs if row["name"] == baseline_name)
                    url = f"/api/evaluation/runs/{baseline['id']}"
                    detail = client.get(url + "/cases/scenario-03").json()
                    assert detail["status"] == "failed" and detail["reviews"]
                    check = client.post(url + "/checks", headers=headers).json()
                    assert check["payload"]["status"] == "completed" and check["payload"]["model_calls"] == 0
                    assert check["created_at"] < runs[0]["created_at"]  # Existing check, preceding the XSS fixture import.
                    browser_summary = json.loads((output / "browser-summary.json").read_text(encoding="utf-8"))
                    if browser_summary.get("workspace"):
                        workflow = browser_summary["workspace"]
                        saved_job = client.get(f"/api/evaluation/jobs/{workflow['job_id']}").json()
                        assert saved_job["status"] == "completed" and saved_job["processed"] == 800
                        saved_queue = client.get("/api/evaluation/review-queue").json()["items"]
                        assert next(item for item in saved_queue if item["id"] == workflow["queue_id"])["status"] == "needs_adjudication"
                    if browser_summary.get("catalog"):
                        catalog = browser_summary["catalog"]
                        saved_item = client.get(f"/api/data/improvements/{catalog['item_id']}").json()
                        assert saved_item["revision"] == 1 and saved_item["events"][0]["payload"]["result"]["suite_counts"]["regressed"] == 3
                        assert client.get(f"/api/data/snapshots/{catalog['snapshot_id']}").json()["quality"]["chunks"] == 101
                summary_path = output / "browser-summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["checks"].append("server restart preserves runs, reviews and original offline check")
                summary["restart_verified"] = True
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            finally:
                stop()
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected if path.exists()}
    assert before == after, "Default business DB or assets changed"
    (output / "protected-assets.json").write_text(json.dumps({"before": before, "after": after, "unchanged": True}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "restart_verified": True, "protected_assets_unchanged": True, "model_calls": 0}))


if __name__ == "__main__":
    main()
