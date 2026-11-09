"""Versioned evaluation projections. Original imports and their judgments stay immutable."""
from __future__ import annotations

import difflib
import json
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from storage import evaluation_store as store


class Metric(store.StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    dimension: Literal["retrieval", "citation", "semantic", "abstention", "execution"]
    kind: Literal["code", "ai", "human"]
    evaluator_version: str = Field(min_length=1, max_length=180)
    status: Literal["passed", "failed", "unknown", "error"]
    value: float | None = Field(default=None, allow_inf_nan=False)
    reason: str = Field(default="", max_length=10000)


class Attempt(store.Result):
    attempt_no: int = Field(default=1, ge=1, le=20)
    metrics: list[Metric] = Field(default_factory=list, max_length=64)
    trace: dict = Field(default_factory=dict)
    events: list[dict] = Field(default_factory=list, max_length=300)
    request_id: str | None = Field(default=None, max_length=180)
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    known_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_unknown: bool = True

    @model_validator(mode="after")
    def unique_metrics(self):
        if len({metric.id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate metric IDs")
        return self


class BundleV2(store.Bundle):
    schema_version: Literal[2]
    dataset_key: str = Field(min_length=1, max_length=180)
    version_label: str = Field(default="", max_length=180)
    parent_version_id: str | None = None
    repetitions: int = Field(default=1, ge=1, le=20)
    results: list[Attempt] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [case.id for case in self.cases]
        keys = [(result.id, result.attempt_no) for result in self.results]
        if (len(set(ids)) != len(ids) or len(set(keys)) != len(keys)
                or any(result.id not in ids or result.attempt_no > self.repetitions for result in self.results)
                or len(ids) * self.repetitions > 10000):
            raise ValueError("Invalid case/attempt identity or planned size")
        if any(len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
               for digest in self.artifacts.values()):
            raise ValueError("Artifact digests must be SHA256")
        return self


BUNDLE_ADAPTER = TypeAdapter(store.Bundle | BundleV2)


def parse_bundle(value):
    return BUNDLE_ADAPTER.validate_json(value) if isinstance(value, (str, bytes, bytearray)) else BUNDLE_ADAPTER.validate_python(value)


DDL = (
    "CREATE TABLE eval_meta (version INTEGER NOT NULL)",
    "CREATE TABLE eval_datasets (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL)",
    "CREATE TABLE eval_rubrics (id TEXT PRIMARY KEY, criteria TEXT NOT NULL)",
    """CREATE TABLE eval_versions (id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, content_sha TEXT NOT NULL,
         rubric_id TEXT NOT NULL, parent_id TEXT, label TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE eval_case_versions (version_id TEXT NOT NULL, case_id TEXT NOT NULL, case_version TEXT NOT NULL,
         question TEXT NOT NULL, contract TEXT NOT NULL, PRIMARY KEY(version_id, case_id))""",
    """CREATE TABLE eval_experiments (run_id TEXT PRIMARY KEY, version_id TEXT NOT NULL, created_at TEXT NOT NULL,
         name TEXT NOT NULL, visibility TEXT NOT NULL, config TEXT NOT NULL, known_cost REAL, cost_unknown INTEGER NOT NULL,
         repetitions INTEGER NOT NULL)""",
    """CREATE TABLE eval_attempts (run_id TEXT NOT NULL, case_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
         case_version TEXT NOT NULL, status TEXT NOT NULL, result TEXT, PRIMARY KEY(run_id,case_id,attempt_no))""",
    """CREATE TABLE eval_scores (run_id TEXT NOT NULL, case_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
         id TEXT NOT NULL, dimension TEXT NOT NULL, kind TEXT NOT NULL, evaluator_version TEXT NOT NULL,
         status TEXT NOT NULL, value REAL, reason TEXT NOT NULL, input_sha TEXT NOT NULL, rubric_id TEXT NOT NULL,
         PRIMARY KEY(run_id,case_id,attempt_no,id))""",
    """CREATE TABLE eval_jobs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, command TEXT NOT NULL, actor TEXT NOT NULL,
         request_id TEXT NOT NULL, status TEXT NOT NULL, snapshot_sha TEXT NOT NULL, auditor_sha TEXT NOT NULL,
         planned INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, lease_token TEXT,
         heartbeat REAL, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT,
         UNIQUE(run_id,command,actor,request_id))""",
    """CREATE TABLE eval_job_results (job_id TEXT NOT NULL, case_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
         status TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,case_id,attempt_no))""",
    """CREATE TABLE eval_review_queue (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, case_id TEXT NOT NULL,
         attempt_no INTEGER NOT NULL, rubric_id TEXT NOT NULL, status TEXT NOT NULL, assignee TEXT,
         lock_token TEXT, locked_until REAL, first_reviewer TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
         UNIQUE(run_id,case_id,attempt_no,rubric_id))""",
    """CREATE TABLE eval_attempt_reviews (id TEXT PRIMARY KEY, queue_id TEXT, run_id TEXT NOT NULL,
         case_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, actor TEXT NOT NULL, request_id TEXT NOT NULL,
         created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id,case_id,attempt_no,actor,request_id))""",
    "CREATE INDEX eval_attempts_status ON eval_attempts(run_id,status,case_id)",
    "CREATE INDEX eval_review_status ON eval_review_queue(status,updated_at)",
)


def ready(connection=None):
    if connection is None:
        with store.database() as connection:
            return ready(connection)
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='eval_meta' AND type='table'").fetchone()
    if not exists:
        return False
    row = connection.execute("SELECT version FROM eval_meta").fetchone()
    if row is None or row[0] != 2:
        raise ValueError("Unsupported evaluation schema version")
    return True


def require_v2(connection):
    if not ready(connection):
        raise ValueError("Run scripts/migrate_evaluation.py on the isolated evaluation database first")


def migrate():
    with store.database() as connection:
        if ready(connection):
            return {"version": 2, "upgraded": False}
        connection.execute("BEGIN IMMEDIATE")
        for statement in DDL:
            connection.execute(statement)
        rows = connection.execute("SELECT id,payload FROM eval_runs ORDER BY created_at,id").fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if store.digest(payload) != row["id"]:
                raise ValueError("Legacy snapshot changed; migration rolled back")
            parse_bundle(payload)
            project(connection, row["id"], payload)
        connection.execute("INSERT INTO eval_meta VALUES (2)")
    return {"version": 2, "upgraded": True, "backfilled_runs": len(rows)}


def metric_rows(case, result, criteria):
    if result is None:
        return []
    rows = []
    for key, passed in result["checks"].items():
        dimension = ("retrieval" if key == "target_retrieved" else "citation" if key == "citation_structure"
                     else "abstention" if "abstention" in key else "execution")
        rows.append({"id": "check:" + key, "dimension": dimension, "kind": "code", "evaluator_version": "legacy-import-v1",
                     "status": "passed" if passed else "failed", "value": float(passed), "reason": "Saved check; original evaluator identity not independently verified"})
    semantic = result["semantic_passed"]
    rows.append({"id": "semantic_support", "dimension": "semantic", "kind": "ai", "evaluator_version": "legacy-import-v1",
                 "status": "unknown" if semantic is None else "passed" if semantic else "failed",
                 "value": None if semantic is None else float(semantic),
                 "reason": result.get("semantic_reason", "") + " | " + result.get("semantic_reviewer", "")})
    explicit = {row["id"]: row for row in result.get("metrics", [])}
    rows = [row for row in rows if row["id"] not in explicit] + list(explicit.values())
    input_sha = store.digest({"case": case, "answer_sha256": result["answer_sha256"], "contexts": result["contexts"]})
    return [row | {"input_sha": input_sha, "rubric_id": store.digest(criteria)} for row in rows]


def attempt_status(result, metrics):
    if result is None:
        return "unknown"
    statuses = [row["status"] for row in metrics]
    if any(status in {"failed", "error"} for status in statuses):
        return "failed"
    if not metrics or "unknown" in statuses or not any(row["dimension"] == "execution" for row in metrics):
        return "unknown"
    return "passed"


def project(connection, identifier, bundle):
    if connection.execute("SELECT 1 FROM eval_experiments WHERE run_id=?", (identifier,)).fetchone():
        return
    rubric = store.digest(bundle["criteria"])
    logical = store.digest({"key": bundle.get("dataset_key", bundle["dataset_name"]), "role": bundle["role"]})
    version = store.digest({"dataset": logical, "content": store.dataset_id(bundle)})
    parent = bundle.get("parent_version_id")
    if parent:
        existing = connection.execute("SELECT dataset_id FROM eval_versions WHERE id=?", (parent,)).fetchone()
        if existing is None or existing[0] != logical or parent == version:
            raise ValueError("Parent version must belong to the same dataset")
    connection.execute("INSERT OR IGNORE INTO eval_datasets VALUES (?,?,?)", (logical, bundle["dataset_name"], bundle["role"]))
    connection.execute("INSERT OR IGNORE INTO eval_rubrics VALUES (?,?)", (rubric, bundle["criteria"]))
    created_at = connection.execute("SELECT created_at FROM eval_runs WHERE id=?", (identifier,)).fetchone()[0]
    connection.execute("INSERT OR IGNORE INTO eval_versions VALUES (?,?,?,?,?,?,?)",
                       (version, logical, store.dataset_id(bundle), rubric, parent, bundle.get("version_label", ""), created_at))
    repetitions = bundle.get("repetitions", 1)
    connection.execute("INSERT INTO eval_experiments VALUES (?,?,?,?,?,?,?,?,?)", (identifier, version, created_at,
                       bundle["name"], bundle["visibility"], store.canonical(bundle["config"]), bundle["known_cost_usd"],
                       int(bundle["cost_unknown"]), repetitions))
    results = {(row["id"], row.get("attempt_no", 1)): row for row in bundle["results"]}
    for case in bundle["cases"]:
        case_version = store.digest(case)
        connection.execute("INSERT OR IGNORE INTO eval_case_versions VALUES (?,?,?,?,?)",
                           (version, case["id"], case_version, case["question"], store.canonical(case["contract"])))
        for number in range(1, repetitions + 1):
            result = results.get((case["id"], number))
            metrics = metric_rows(case, result, bundle["criteria"])
            connection.execute("INSERT INTO eval_attempts VALUES (?,?,?,?,?,?)",
                               (identifier, case["id"], number, case_version, attempt_status(result, metrics),
                                store.canonical(result) if result is not None else None))
            connection.executemany("INSERT INTO eval_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                (identifier, case["id"], number, row["id"], row["dimension"], row["kind"], row["evaluator_version"],
                 row["status"], row["value"], row["reason"], row["input_sha"], rubric) for row in metrics])


def experiment(connection, identifier):
    require_v2(connection)
    row = connection.execute("""SELECT e.*,v.dataset_id,v.content_sha,v.rubric_id,d.name AS dataset_name,d.role
        FROM eval_experiments e JOIN eval_versions v ON e.version_id=v.id JOIN eval_datasets d ON v.dataset_id=d.id
        WHERE e.run_id=?""", (identifier,)).fetchone()
    if row is None:
        raise KeyError("Experiment not found")
    return dict(row)


def case_counts(connection, identifier):
    rows = connection.execute("""SELECT CASE WHEN SUM(status='failed')>0 THEN 'failed'
        WHEN SUM(status='unknown')>0 THEN 'unknown' ELSE 'passed' END AS status,case_id
        FROM eval_attempts WHERE run_id=? GROUP BY case_id""", (identifier,)).fetchall()
    return {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "unknown")}


def summary(connection, identifier):
    e = experiment(connection, identifier)
    count = connection.execute("SELECT COUNT(*),COUNT(result) FROM eval_attempts WHERE run_id=?", (identifier,)).fetchone()
    counts = case_counts(connection, identifier)
    return {"id": identifier, "dataset_id": e["content_sha"], "logical_dataset_id": e["dataset_id"],
            "version_id": e["version_id"], "created_at": e["created_at"], "name": e["name"],
            "dataset_name": e["dataset_name"], "role": e["role"], "visibility": e["visibility"],
            "planned": sum(counts.values()), "recorded": connection.execute(
                "SELECT COUNT(DISTINCT case_id) FROM eval_attempts WHERE run_id=? AND result IS NOT NULL", (identifier,)).fetchone()[0],
            "planned_attempts": count[0], "recorded_attempts": count[1], "repetitions": e["repetitions"],
            "counts": counts, "known_cost_usd": e["known_cost"], "cost_unknown": bool(e["cost_unknown"]),
            "independent_human_gold": False}


def summaries(connection):
    identifiers = [row[0] for row in connection.execute("SELECT run_id FROM eval_experiments ORDER BY created_at DESC,run_id")]
    return [summary(connection, identifier) for identifier in identifiers]


def cases(identifier, status="all", query="", offset=0, limit=20):
    with store.database() as connection:
        e = experiment(connection, identifier)
        # Aggregate status is explicitly "any failure / any unknown / all pass" across attempts.
        sql = """WITH grouped AS (SELECT case_id,COUNT(*) AS attempts,SUM(status='passed') AS passed_attempts,
            CASE WHEN SUM(status='failed')>0 THEN 'failed' WHEN SUM(status='unknown')>0 THEN 'unknown' ELSE 'passed' END AS status
            FROM eval_attempts WHERE run_id=? GROUP BY case_id)
            SELECT c.*,g.status,g.attempts,g.passed_attempts FROM eval_case_versions c JOIN grouped g ON c.case_id=g.case_id
            WHERE c.version_id=? AND (?='all' OR g.status=?) AND (instr(lower(c.case_id),lower(?))>0 OR instr(lower(c.question),lower(?))>0)"""
        params = (identifier, e["version_id"], status, status, query, query)
        total = connection.execute("SELECT COUNT(*) FROM (" + sql + ")", params).fetchone()[0]
        rows = connection.execute(sql + " ORDER BY c.case_id LIMIT ? OFFSET ?", params + (limit, offset)).fetchall()
    return {"total": total, "offset": offset, "limit": limit,
            "items": [{"id": row["case_id"], "question": row["question"], "contract": json.loads(row["contract"]),
                       "case_version": row["case_version"], "status": row["status"], "attempts": row["attempts"],
                       "passed_attempts": row["passed_attempts"]} for row in rows]}


def detail(identifier, case_id, attempt=1):
    with store.database() as connection:
        e = experiment(connection, identifier)
        case = connection.execute("SELECT * FROM eval_case_versions WHERE version_id=? AND case_id=?", (e["version_id"], case_id)).fetchone()
        row = connection.execute("SELECT * FROM eval_attempts WHERE run_id=? AND case_id=? AND attempt_no=?", (identifier, case_id, attempt)).fetchone()
        if case is None or row is None:
            raise KeyError("Case/attempt not found")
        scores = [dict(score) for score in connection.execute("SELECT * FROM eval_scores WHERE run_id=? AND case_id=? AND attempt_no=? ORDER BY dimension,id",
                                                             (identifier, case_id, attempt))]
        attempts = [dict(item) for item in connection.execute("SELECT attempt_no,status FROM eval_attempts WHERE run_id=? AND case_id=? ORDER BY attempt_no", (identifier, case_id))]
        reviews = [dict(item) for item in connection.execute("SELECT * FROM eval_attempt_reviews WHERE run_id=? AND case_id=? AND attempt_no=? ORDER BY created_at,id", (identifier, case_id, attempt))]
        if attempt == 1:
            reviews += [dict(item) for item in connection.execute("SELECT * FROM eval_reviews WHERE run_id=? AND case_id=?", (identifier, case_id))]
    for review in reviews:
        review["payload"] = json.loads(review["payload"])
    return {"case": {"id": case_id, "question": case["question"], "contract": json.loads(case["contract"])},
            "case_version": case["case_version"], "rubric_id": e["rubric_id"], "attempt_no": attempt,
            "attempts": attempts, "metrics": scores, "result": json.loads(row["result"]) if row["result"] else None,
            "status": row["status"], "reviews": sorted(reviews, key=lambda item: (item["created_at"], item["id"]))}


def datasets():
    with store.database() as connection:
        require_v2(connection)
        rows = connection.execute("""SELECT d.id,d.name,d.role,v.id AS version_id,v.parent_id,v.label,v.created_at,v.rubric_id,
            (SELECT COUNT(*) FROM eval_case_versions c WHERE c.version_id=v.id) AS cases,
            (SELECT COUNT(*) FROM eval_experiments e WHERE e.version_id=v.id) AS experiments
            FROM eval_datasets d JOIN eval_versions v ON d.id=v.dataset_id ORDER BY v.created_at DESC,v.id""").fetchall()
    return [dict(row) for row in rows]


def compare(left_id, right_id):
    with store.database() as connection:
        left, right = experiment(connection, left_id), experiment(connection, right_id)
    a, b = ({item["id"]: item for item in cases(identifier, limit=2000)["items"]} for identifier in (left_id, right_id))
    rows = []
    with store.database() as connection:
        signatures = []
        for run in (left_id, right_id):
            grouped = {}
            for score in connection.execute("SELECT case_id,attempt_no,id,dimension,status,value,reason,evaluator_version FROM eval_scores WHERE run_id=? ORDER BY case_id,attempt_no,id", (run,)):
                grouped.setdefault(score["case_id"], []).append(tuple(score)[1:])
            signatures.append(grouped)
    same = left["rubric_id"] == right["rubric_id"] and left["role"] == right["role"]
    for identifier in sorted(a.keys() | b.keys()):
        before, after = a.get(identifier, {}).get("status", "unknown"), b.get(identifier, {}).get("status", "unknown")
        change = ("added" if identifier not in a else "removed" if identifier not in b else "incomparable"
                  if not same or a[identifier]["case_version"] != b[identifier]["case_version"] else "insufficient_evidence"
                  if "unknown" in (before, after) else "unchanged" if before == after else "fixed" if after == "passed" else "regressed")
        rows.append({"id": identifier, "question": (a.get(identifier) or b[identifier])["question"], "before": before,
                     "after": after, "change": change, "left_attempts": a.get(identifier, {}).get("attempts", 0),
                     "right_attempts": b.get(identifier, {}).get("attempts", 0),
                     "reason_changed": signatures[0].get(identifier) != signatures[1].get(identifier)})
    ac, bc = json.loads(left["config"]), json.loads(right["config"])
    return {"left": left_id, "right": right_id, "config_changed": ac != bc, "criteria_match": same,
            "config_diff": [{"key": key, "before": ac.get(key), "after": bc.get(key), "before_present": key in ac,
                             "after_present": key in bc} for key in sorted(ac.keys() | bc.keys()) if ac.get(key) != bc.get(key) or (key in ac) != (key in bc)],
            "counts": {key: sum(row["change"] == key for row in rows) for key in (
                "fixed", "regressed", "unchanged", "incomparable", "insufficient_evidence", "added", "removed")},
            "items": rows, "scope": "Historical judgments; unchanged means aggregate status only; repeats are not independent cases"}


def comparison_detail(left, right, case_id, left_attempt=1, right_attempt=1):
    with store.database() as connection:
        same_role = experiment(connection, left)["role"] == experiment(connection, right)["role"]
    result = {}
    for side, identifier, attempt in (("left", left, left_attempt), ("right", right, right_attempt)):
        try:
            result[side] = detail(identifier, case_id, attempt)
        except KeyError:
            result[side] = None
    if not any(result.values()):
        raise KeyError("Case not present")
    av, bv = result["left"], result["right"]
    comparable = bool(same_role and av and bv and av["case_version"] == bv["case_version"] and av["rubric_id"] == bv["rubric_id"])
    am, bm = ({row["id"]: row for row in value["metrics"]} if value else {} for value in (av, bv))
    metric_diff = [{"id": key, "before": am.get(key), "after": bm.get(key),
                    "changed": am.get(key) is None or bm.get(key) is None or any(am[key][field] != bm[key][field]
                    for field in ("status", "value", "reason", "evaluator_version"))} for key in sorted(am.keys() | bm.keys())]
    answers = [value["result"]["answer"] if value and value["result"] else "" for value in (av, bv)]
    return result | {"comparable": comparable, "metrics": metric_diff,
                     "reason_changed": any(row["changed"] for row in metric_diff),
                     "answer_diff": "\n".join(difflib.unified_diff(answers[0].splitlines(), answers[1].splitlines(),
                                                                  fromfile="baseline", tofile="candidate", lineterm=""))}


def export_report(identifier):
    with store.database() as connection:
        e = experiment(connection, identifier)
        counts = case_counts(connection, identifier)
        rows = connection.execute("SELECT case_version,attempt_no,status,case_id FROM eval_attempts WHERE run_id=? ORDER BY case_id,attempt_no", (identifier,)).fetchall()
        comments = connection.execute("SELECT actor,created_at,payload,case_id,attempt_no FROM eval_attempt_reviews WHERE run_id=? ORDER BY created_at", (identifier,)).fetchall()
        comments = [dict(row) for row in comments] + [dict(row) | {"attempt_no": 1} for row in connection.execute(
            "SELECT actor,created_at,payload,case_id FROM eval_reviews WHERE run_id=? ORDER BY created_at", (identifier,))]
    case_hashes = {row["case_id"]: row["case_version"] for row in rows}
    return {"run": {"id": identifier, "version_id": e["version_id"], "planned": sum(counts.values()),
                    "planned_attempts": len(rows), "counts": counts, "known_cost_usd": e["known_cost"],
                    "cost_unknown": bool(e["cost_unknown"]), "independent_human_gold": False},
            "results": [{"case_hash": row["case_version"], "attempt_no": row["attempt_no"], "status": row["status"]} for row in rows],
            "reviews": [{"case_hash": case_hashes[row["case_id"]], "attempt_no": row["attempt_no"],
                         "actor_hash": store.digest(row["actor"]), "created_at": row["created_at"],
                         "decision": json.loads(row["payload"])["decision"], "kind": json.loads(row["payload"])["kind"],
                         "answer_sha256": json.loads(row["payload"])["answer_sha256"]} for row in comments],
            "scope": "Redacted development evidence; raw questions, answers, traces, configs and review prose omitted"}
