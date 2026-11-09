"""Persistent zero-model audit jobs. Single global claim; explicit resume and fenced writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from pydantic import Field

from storage import evaluation_store as store, evaluation_workspace as workspace

COMMAND = "saved-http-audit-v1"
LEASE_SECONDS = 60


class JobRequest(store.StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    command: str = COMMAND


def auditor_sha():
    from scripts import audit_flash_answers
    from agents import sources

    return store.digest({name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in (
        ("audit_flash_answers.py", audit_flash_answers.__file__), ("evaluation_jobs.py", __file__), ("sources.py", sources.__file__))})


def frozen_run(identifier):
    row = store.get_run(identifier)
    if store.digest(row["payload"]) != identifier:
        raise ValueError("Frozen import changed")
    workspace.parse_bundle(row["payload"])
    bundle = row["payload"]
    indexed = {(result["id"], result.get("attempt_no", 1)): result for result in bundle["results"]}
    expected = sorted((case["id"], number, store.digest(case), store.canonical(indexed.get((case["id"], number))))
                      for case in bundle["cases"] for number in range(1, bundle.get("repetitions", 1) + 1))
    with store.database() as connection:
        workspace.experiment(connection, identifier)
        rows = connection.execute("SELECT case_id,attempt_no,case_version,result FROM eval_attempts WHERE run_id=? ORDER BY case_id,attempt_no", (identifier,)).fetchall()
    actual = [(item[0], item[1], item[2], item[3] if item[3] is not None else "null") for item in rows]
    if expected != actual:
        raise ValueError("Normalized inputs differ from the frozen import")
    return store.digest({"import": identifier, "attempts": actual})


def create(identifier, request, actor):
    if request.command != COMMAND:
        raise ValueError("Unknown offline auditor")
    frozen = frozen_run(identifier)
    with store.database() as connection:
        workspace.experiment(connection, identifier)
        planned = connection.execute("SELECT COUNT(*) FROM eval_attempts WHERE run_id=?", (identifier,)).fetchone()[0]
        stamp = store.now()
        connection.execute("""INSERT OR IGNORE INTO eval_jobs
            (id,run_id,command,actor,request_id,status,snapshot_sha,auditor_sha,planned,created_at,updated_at)
            VALUES (?,?,?,?,?,'queued',?,?,?,?,?)""",
                           (uuid4().hex, identifier, COMMAND, actor, request.request_id, frozen, auditor_sha(), planned, stamp, stamp))
        row = connection.execute("SELECT id,auditor_sha FROM eval_jobs WHERE run_id=? AND command=? AND actor=? AND request_id=?",
                                 (identifier, COMMAND, actor, request.request_id)).fetchone()
        stored = connection.execute("SELECT snapshot_sha FROM eval_jobs WHERE id=?", (row["id"],)).fetchone()[0]
        if row["auditor_sha"] != auditor_sha() or stored != frozen:
            raise ValueError("Idempotency key belongs to a different auditor version")
    return get(row["id"])


def effective_status(row):
    if row["status"] == "running" and (row["heartbeat"] is None or row["heartbeat"] < time.time() - LEASE_SECONDS):
        return "interrupted"
    return row["status"]


def get(identifier):
    with store.database() as connection:
        workspace.require_v2(connection)
        row = connection.execute("SELECT * FROM eval_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        results = connection.execute("SELECT case_id,attempt_no,status,report,created_at FROM eval_job_results WHERE job_id=? ORDER BY case_id,attempt_no", (identifier,)).fetchall()
    data = dict(row)
    data.pop("lease_token", None)
    data["status"] = effective_status(row)
    data["processed"] = len(results)
    data["failed_checks"] = sum(item["status"] == "failed" for item in results)
    data["errors"] = sum(item["status"] in {"error", "missing"} for item in results)
    data["results"] = [dict(item) | {"report": json.loads(item["report"])} for item in results]
    data["model_calls"] = 0
    data["semantic_acceptance"] = False
    return data


def list_jobs():
    with store.database() as connection:
        workspace.require_v2(connection)
        identifiers = [row[0] for row in connection.execute("SELECT id FROM eval_jobs ORDER BY created_at DESC,id LIMIT 100")]
    return [get(identifier) for identifier in identifiers]


def cancel(identifier):
    with store.database() as connection:
        workspace.require_v2(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM eval_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        status = effective_status(row)
        if status in {"queued", "interrupted"}:
            connection.execute("UPDATE eval_jobs SET status='cancelled',cancel_requested=1,lease_token=NULL,updated_at=? WHERE id=?", (store.now(), identifier))
        elif status == "running":
            connection.execute("UPDATE eval_jobs SET cancel_requested=1,updated_at=? WHERE id=?", (store.now(), identifier))
    return get(identifier)


def resume(identifier):
    original = get(identifier)
    if frozen_run(original["run_id"]) != original["snapshot_sha"]:
        raise ValueError("Frozen job inputs changed")
    if original["auditor_sha"] != auditor_sha():
        raise ValueError("Auditor version changed; create a new frozen job")
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM eval_jobs WHERE id=?", (identifier,)).fetchone()
        if effective_status(row) not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("Only stopped jobs can be resumed explicitly")
        connection.execute("""UPDATE eval_jobs SET status='queued',cancel_requested=0,lease_token=NULL,heartbeat=NULL,
            error=NULL,updated_at=? WHERE id=?""", (store.now(), identifier))
    return get(identifier)


def claim():
    with store.database() as connection:
        workspace.require_v2(connection)
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM eval_jobs WHERE status='running' AND heartbeat>?", (time.time() - LEASE_SECONDS,)).fetchone():
            return None
        row = connection.execute("SELECT * FROM eval_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if row is None:
            return None
        token = uuid4().hex
        connection.execute("UPDATE eval_jobs SET status='running',lease_token=?,heartbeat=?,updated_at=? WHERE id=?",
                           (token, time.time(), store.now(), row["id"]))
    return dict(row) | {"lease_token": token}


def audit_attempt(case, result):
    from scripts.audit_flash_answers import citation_checks

    if result is None:
        return {"status": "missing", "checks": {}, "reason": "No recorded answer; not a passing audit", "semantic_acceptance": False}
    applicable = not ((case["contract"].get("expected_intent", "knowledge") != "knowledge"
                      or ("target_locator" in case["contract"] and case["contract"]["target_locator"] is None))
                     and not result["contexts"])
    report = citation_checks(result["answer"], result["contexts"]) if applicable else {"status": "not_applicable", "errors": []}
    checks = {"answer_hash": hashlib.sha256(result["answer"].encode()).hexdigest() == result["answer_sha256"]}
    if applicable:
        checks["citation_structure"] = not report["errors"]
    locator = case["contract"].get("target_locator")
    if locator is not None:
        checks["target_retrieved"] = locator in [context.get("locator") for context in result["contexts"]]
    events = result.get("events", [])
    if events:
        checks["sse_done"] = events[-1].get("type") == "done" and not any(event.get("type") == "error" for event in events)
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "citation_audit": report, "semantic_acceptance": False, "model_calls": 0}


def execute(job, stop_event=None):
    identifier, token = job["id"], job["lease_token"]
    try:
        if frozen_run(job["run_id"]) != job["snapshot_sha"]:
            raise ValueError("Frozen job inputs changed")
        if auditor_sha() != job["auditor_sha"]:
            raise ValueError("Auditor version changed")
        with store.database() as connection:
            rows = connection.execute("""SELECT a.case_id,a.attempt_no FROM eval_attempts a WHERE a.run_id=? AND NOT EXISTS
                (SELECT 1 FROM eval_job_results r WHERE r.job_id=? AND r.case_id=a.case_id AND r.attempt_no=a.attempt_no)
                ORDER BY a.case_id,a.attempt_no""", (job["run_id"], identifier)).fetchall()
        for item in rows:
            with store.database() as connection:
                current = connection.execute("SELECT * FROM eval_jobs WHERE id=?", (identifier,)).fetchone()
                if current["lease_token"] != token or current["status"] != "running":
                    return
                if current["heartbeat"] < time.time() - LEASE_SECONDS:
                    connection.execute("UPDATE eval_jobs SET status='interrupted',lease_token=NULL,updated_at=? WHERE id=? AND lease_token=?",
                                       (store.now(), identifier, token))
                    return
                status = "cancelled" if current["cancel_requested"] else "interrupted" if stop_event and stop_event.is_set() else None
                if status:
                    connection.execute("UPDATE eval_jobs SET status=?,lease_token=NULL,updated_at=? WHERE id=? AND lease_token=?",
                                       (status, store.now(), identifier, token))
                    return
                connection.execute("UPDATE eval_jobs SET heartbeat=? WHERE id=? AND lease_token=?", (time.time(), identifier, token))
            detail = workspace.detail(job["run_id"], item["case_id"], item["attempt_no"])
            try:
                report = audit_attempt(detail["case"], detail["result"])
            except Exception as exc:
                report = {"status": "error", "error_type": type(exc).__name__, "semantic_acceptance": False}
            with store.database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT status,lease_token,heartbeat FROM eval_jobs WHERE id=?", (identifier,)).fetchone()
                if current["lease_token"] != token or current["status"] != "running":
                    return
                if current["heartbeat"] < time.time() - LEASE_SECONDS:
                    connection.execute("UPDATE eval_jobs SET status='interrupted',lease_token=NULL,updated_at=? WHERE id=? AND lease_token=?",
                                       (store.now(), identifier, token))
                    return
                connection.execute("INSERT OR IGNORE INTO eval_job_results VALUES (?,?,?,?,?,?)",
                                   (identifier, item["case_id"], item["attempt_no"], report["status"], store.canonical(report), store.now()))
                connection.execute("UPDATE eval_jobs SET heartbeat=?,updated_at=? WHERE id=? AND lease_token=?", (time.time(), store.now(), identifier, token))
        if frozen_run(job["run_id"]) != job["snapshot_sha"] or auditor_sha() != job["auditor_sha"]:
            raise ValueError("Frozen inputs or auditor changed during execution")
        with store.database() as connection:
            bad = connection.execute("SELECT COUNT(*) FROM eval_job_results WHERE job_id=? AND status IN ('error','missing')", (identifier,)).fetchone()[0]
            connection.execute("""UPDATE eval_jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END,
                lease_token=NULL,updated_at=? WHERE id=? AND lease_token=? AND status='running'""",
                               ("failed" if bad else "completed", store.now(), identifier, token))
    except Exception as exc:
        with store.database() as connection:
            connection.execute("UPDATE eval_jobs SET status='failed',error=?,lease_token=NULL,updated_at=? WHERE id=? AND lease_token=?",
                               (type(exc).__name__, store.now(), identifier, token))


def run_worker(stop_event):
    while not stop_event.is_set():
        job = claim()
        if job:
            execute(job, stop_event)
        else:
            stop_event.wait(.25)


def start_worker():
    if os.getenv("CISP_EVAL_WORKER_ENABLED", "false").lower() != "true":
        return None
    if not workspace.ready():
        raise ValueError("Upgrade the isolated evaluation database before enabling its worker")
    stop_event = threading.Event()
    thread = threading.Thread(target=run_worker, args=(stop_event,), daemon=True, name="evaluation-offline-worker")
    thread.start()
    return stop_event, thread


def stop_worker(worker):
    if worker:
        worker[0].set()
        worker[1].join(timeout=5)
