"""Isolated, immutable evaluation imports and append-only review records. No model calls."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Case(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=10000)
    contract: dict


class Result(StrictModel):
    id: str
    answer: str = Field(max_length=100000)
    contexts: list[dict] = Field(default_factory=list, max_length=30)
    checks: dict[str, bool] = Field(default_factory=dict)
    semantic_passed: bool | None = None
    semantic_reviewer: str = Field(default="", max_length=500)
    semantic_reason: str = Field(default="", max_length=10000)
    answer_sha256: str

    @model_validator(mode="after")
    def bound_review(self):
        if self.answer_sha256 != hashlib.sha256(self.answer.encode()).hexdigest():
            raise ValueError("Answer hash mismatch")
        if self.semantic_passed is not None and not self.semantic_reviewer.strip():
            raise ValueError("Semantic decision requires reviewer attribution")
        return self


class Bundle(StrictModel):
    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=180)
    dataset_name: str = Field(min_length=1, max_length=180)
    role: Literal["development", "regression", "unseen", "synthetic"]
    visibility: Literal["public", "private", "synthetic"]
    criteria: str = Field(min_length=1, max_length=4000)
    config: dict
    artifacts: dict[str, str]
    cases: list[Case] = Field(min_length=1, max_length=2000)
    results: list[Result] = Field(max_length=2000)
    known_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_unknown: bool = True

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [row.id for row in self.cases]
        results = [row.id for row in self.results]
        if len(set(ids)) != len(ids) or len(set(results)) != len(results) or set(results) - set(ids):
            raise ValueError("Duplicate or unknown case/result IDs")
        if any(len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
               for digest in self.artifacts.values()):
            raise ValueError("Artifact digests must be SHA256")
        return self


class Review(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    answer_sha256: str
    decision: Literal["supported", "unsupported", "needs_evidence"]
    reason: str = Field(min_length=1, max_length=4000)
    kind: Literal["human", "ai_assisted"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    path = Path(os.getenv("CISP_EVAL_DB_PATH", "data/evaluation/platform.sqlite3")).resolve()
    # Never let a configuration typo turn the platform store into the business DB.
    import config

    forbidden = {Path(config.DB_PATH).resolve(), Path(config.PROJECT_ROOT, "data/cisp_qa.db").resolve()}
    if config.MYSQL_URL_OVERRIDE.strip().startswith("sqlite:"):
        from sqlalchemy.engine import make_url

        target = make_url(config.MYSQL_URL_OVERRIDE).database
        if target and target != ":memory:":
            forbidden.add(Path(target).resolve())
    if any(path == target or (path.exists() and target.exists() and path.samefile(target)) for target in forbidden):
        raise ValueError("Evaluation storage must be separate from business storage")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        allowed = {"eval_runs", "eval_reviews", "eval_checks", "eval_meta", "eval_datasets", "eval_versions",
                   "eval_case_versions", "eval_experiments", "eval_attempts", "eval_scores", "eval_rubrics",
                   "eval_jobs", "eval_job_results", "eval_review_queue", "eval_attempt_reviews"}
        if tables - allowed:
            raise ValueError("Refusing to modify an unrelated SQLite database")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS eval_runs (
                id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, created_at TEXT NOT NULL,
                imported_by TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS eval_reviews (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, case_id TEXT NOT NULL,
                request_id TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL, UNIQUE(run_id, case_id, actor, request_id));
            CREATE TABLE IF NOT EXISTS eval_checks (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL,
                payload TEXT NOT NULL);
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def dataset_id(bundle):
    return digest({"role": bundle["role"], "criteria": bundle["criteria"],
                   "cases": sorted(bundle["cases"], key=lambda row: row["id"])})


def import_bundle(bundle, actor):
    payload = bundle.model_dump()
    identifier = digest(payload)
    with database() as connection:
        cursor = connection.execute("INSERT OR IGNORE INTO eval_runs VALUES (?, ?, ?, ?, ?)",
                                    (identifier, dataset_id(payload), now(), actor, canonical(payload)))
        created = cursor.rowcount == 1
        from storage import evaluation_workspace as workspace

        if workspace.ready(connection):
            workspace.project(connection, identifier, payload)
        elif payload["schema_version"] == 2:
            raise ValueError("Explicit evaluation schema upgrade required")
    return {"id": identifier, "created": created}


def get_run(identifier):
    with database() as connection:
        row = connection.execute("SELECT * FROM eval_runs WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError("Run not found")
    return {**dict(row), "payload": json.loads(row["payload"])}


def case_status(result):
    if result is None:
        return "unknown"
    values = list(result["checks"].values()) + [result["semantic_passed"]]
    if False in values:
        return "failed"
    if not result["checks"] or None in values:
        return "unknown"
    return "passed"


def summary(row):
    bundle = row["payload"]
    results = {result["id"]: result for result in bundle["results"]}
    counts = {state: 0 for state in ("passed", "failed", "unknown")}
    for case in bundle["cases"]:
        counts[case_status(results.get(case["id"]))] += 1
    return {key: row[key] for key in ("id", "dataset_id", "created_at")} | {
        "name": bundle["name"], "dataset_name": bundle["dataset_name"], "role": bundle["role"],
        "visibility": bundle["visibility"], "planned": len(bundle["cases"]),
        "recorded": len(results), "counts": counts, "known_cost_usd": bundle["known_cost_usd"],
        "cost_unknown": bundle["cost_unknown"], "independent_human_gold": False}


def list_runs():
    with database() as connection:
        from storage import evaluation_workspace as workspace

        if workspace.ready(connection):
            return workspace.summaries(connection)
        rows = connection.execute("SELECT * FROM eval_runs ORDER BY created_at DESC, id").fetchall()
    return [summary({**dict(row), "payload": json.loads(row["payload"])}) for row in rows]


def cases(identifier, status="all", query="", offset=0, limit=20):
    from storage import evaluation_workspace as workspace

    if workspace.ready():
        return workspace.cases(identifier, status, query, offset, limit)
    bundle = get_run(identifier)["payload"]
    indexed = {row["id"]: row for row in bundle["results"]}
    rows = [{**case, "status": case_status(indexed.get(case["id"]))} for case in bundle["cases"]]
    rows = [row for row in rows if (status == "all" or row["status"] == status)
            and query.casefold() in (row["id"] + row["question"]).casefold()]
    return {"total": len(rows), "items": rows[offset:offset + limit], "offset": offset, "limit": limit}


def detail(identifier, case_id):
    from storage import evaluation_workspace as workspace

    if workspace.ready():
        return workspace.detail(identifier, case_id)
    bundle = get_run(identifier)["payload"]
    case = next((case for case in bundle["cases"] if case["id"] == case_id), None)
    if case is None:
        raise KeyError("Case not found")
    result = next((row for row in bundle["results"] if row["id"] == case_id), None)
    with database() as connection:
        rows = connection.execute("SELECT * FROM eval_reviews WHERE run_id=? AND case_id=? ORDER BY created_at, id",
                                  (identifier, case_id)).fetchall()
    reviews = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
    return {"case": case, "result": result, "status": case_status(result), "reviews": reviews}


def add_review(identifier, case_id, review, actor):
    result = detail(identifier, case_id)["result"]
    if result is None or result["answer_sha256"] != review.answer_sha256:
        raise ValueError("Review must bind to the recorded answer")
    payload = canonical(review.model_dump())
    with database() as connection:
        connection.execute("INSERT OR IGNORE INTO eval_reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (uuid4().hex, identifier, case_id, review.request_id, actor, now(), payload))
        row = connection.execute("SELECT * FROM eval_reviews WHERE run_id=? AND case_id=? AND actor=? AND request_id=?",
                                 (identifier, case_id, actor, review.request_id)).fetchone()
        if row["payload"] != payload:
            raise ValueError("Review request ID reused with different content")
    return {"id": row["id"], "created_at": row["created_at"]}


def compare(left_id, right_id):
    from storage import evaluation_workspace as workspace

    if workspace.ready():
        return workspace.compare(left_id, right_id)
    left, right = get_run(left_id)["payload"], get_run(right_id)["payload"]
    a, b = ({case["id"]: case for case in bundle["cases"]} for bundle in (left, right))
    ar, br = ({row["id"]: row for row in bundle["results"]} for bundle in (left, right))
    same_criteria = left["criteria"] == right["criteria"] and left["role"] == right["role"]
    rows = []
    for identifier in sorted(a.keys() | b.keys()):
        before, after = case_status(ar.get(identifier)), case_status(br.get(identifier))
        if identifier not in a:
            change = "added"
        elif identifier not in b:
            change = "removed"
        elif not same_criteria or a[identifier] != b[identifier]:
            change = "incomparable"
        elif "unknown" in (before, after):
            change = "insufficient_evidence"
        elif before == after:
            change = "unchanged"
        else:
            change = "fixed" if after == "passed" else "regressed"
        rows.append({"id": identifier, "before": before, "after": after, "change": change})
    return {"left": left_id, "right": right_id, "config_changed": left["config"] != right["config"],
            "criteria_match": same_criteria, "counts": {key: sum(row["change"] == key for row in rows)
            for key in ("fixed", "regressed", "unchanged", "incomparable", "insufficient_evidence", "added", "removed")},
            "items": rows, "scope": "Recorded development judgments, not causal or independent acceptance"}


def offline_check(identifier, actor):
    row = get_run(identifier)
    from storage.evaluation_workspace import parse_bundle

    bundle = parse_bundle(row["payload"])
    checks = {"bundle_hash": digest(bundle.model_dump()) == identifier,
              "dataset_hash": dataset_id(bundle.model_dump()) == row["dataset_id"],
              "all_results_recorded": len(bundle.results) == len(bundle.cases)}
    result = {"command": "integrity-v1", "status": "completed" if all(checks.values()) else "failed",
              "checks": checks, "model_calls": 0, "semantic_acceptance": False}
    # Atomic, synchronous tasks: a process interruption cannot leave a false completed row.
    with database() as connection:
        connection.execute("INSERT OR IGNORE INTO eval_checks VALUES (?, ?, ?, ?)",
                           (identifier, now(), actor, canonical(result)))
        saved = connection.execute("SELECT * FROM eval_checks WHERE run_id=?", (identifier,)).fetchone()
        if saved["payload"] != canonical(result):
            raise ValueError("Stored check no longer matches immutable input")
    return {**dict(saved), "payload": json.loads(saved["payload"])}


def export_report(identifier):
    row = get_run(identifier)
    bundle = row["payload"]
    # Safe by construction: never export questions, answers, contexts, config, free-text notes or paths.
    with database() as connection:
        reviews = connection.execute("SELECT case_id, actor, created_at, payload FROM eval_reviews WHERE run_id=? ORDER BY created_at",
                                     (identifier,)).fetchall()
    return {"run": {key: value for key, value in summary(row).items() if key not in {"name", "dataset_name"}},
            "artifacts_sha256": sorted(bundle["artifacts"].values()),
            "results": [{"case_hash": digest(case), "status": case_status(next(
                (result for result in bundle["results"] if result["id"] == case["id"]), None))}
                for case in bundle["cases"]],
            "reviews": [{"case_hash": digest(next(case for case in bundle["cases"] if case["id"] == review["case_id"])),
                         "actor_hash": digest(review["actor"]), "created_at": review["created_at"],
                         "decision": json.loads(review["payload"])["decision"],
                         "kind": json.loads(review["payload"])["kind"],
                         "answer_sha256": json.loads(review["payload"])["answer_sha256"]} for review in reviews],
            "scope": "Redacted development evidence; not independent human gold or production acceptance"}
