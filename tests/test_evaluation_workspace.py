"""V2 contracts: migration, repeated results, independent metrics and frozen offline workflows."""
import copy
import json
import sqlite3
import threading
import time

import pytest
from pydantic import ValidationError

from storage import evaluation_store as store, evaluation_workspace as ws
from storage import evaluation_jobs as jobs, evaluation_reviews as reviews
from test_evaluation_platform import sample


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "workspace.sqlite3"
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(path))
    return path


def add(data=None):
    return store.import_bundle(ws.parse_bundle(data or sample()), "operator")["id"]


def repeated():
    data = sample()
    data.update(schema_version=2, dataset_key="synthetic-snapshot", repetitions=3)
    row = data["results"][0]
    data["results"] = [row | {"attempt_no": n} for n in (1, 2)]
    data["results"][1]["semantic_passed"] = True
    return data


def test_explicit_migration_backfills_unchanged_imports(database):
    identifier = add()
    before = store.get_run(identifier)["payload"]
    result = ws.migrate()
    assert result == {"version": 2, "upgraded": True, "backfilled_runs": 1}
    assert ws.migrate() == {"version": 2, "upgraded": False}
    assert store.get_run(identifier)["payload"] == before
    assert store.list_runs()[0]["counts"] == {"passed": 0, "failed": 1, "unknown": 0}
    assert ws.detail(identifier, "q1")["metrics"]
    with store.database() as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"eval_attempts", "eval_versions", "eval_scores", "eval_jobs", "eval_review_queue"} <= names


def test_migration_rolls_back_if_original_snapshot_was_changed(database):
    identifier = add()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE eval_runs SET payload='{}' WHERE id=?", (identifier,))
    with pytest.raises(ValueError, match="changed"):
        ws.migrate()
    with sqlite3.connect(database) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='eval_meta'").fetchone()


def test_v2_requires_explicit_upgrade_and_unique_attempts(database):
    with pytest.raises(ValueError, match="upgrade"):
        add(repeated())
    assert store.list_runs() == []
    ws.migrate()
    data = repeated()
    identifier = add(data)
    assert ws.detail(identifier, "q1", 1)["status"] == "failed"
    assert ws.detail(identifier, "q1", 2)["status"] == "passed"
    assert ws.detail(identifier, "q1", 3)["status"] == "unknown"
    assert store.list_runs()[0]["planned_attempts"] == 3
    assert store.list_runs()[0]["recorded_attempts"] == 2
    assert store.cases(identifier)["items"][0]["passed_attempts"] == 1
    data["results"].append(copy.deepcopy(data["results"][0]))
    with pytest.raises(ValidationError):
        ws.parse_bundle(data)


def test_version_parent_pagination_and_dimension_differences(database):
    ws.migrate()
    left = repeated()
    a = add(left)
    with store.database() as connection:
        version = ws.experiment(connection, a)["version_id"]
    right = copy.deepcopy(left)
    right["name"] = "candidate"
    right["results"][0]["semantic_reason"] = "Different failure reason"
    b = add(right)
    assert ws.compare(a, b)["counts"]["unchanged"] == 1
    assert ws.comparison_detail(a, b, "q1")["reason_changed"]
    right["cases"][0]["contract"]["revision"] = 2
    right["parent_version_id"] = version
    c = add(right)
    assert ws.compare(a, c)["counts"]["incomparable"] == 1
    assert any(row["parent_id"] == version for row in ws.datasets())
    assert ws.cases(a, query="absent")["total"] == 0
    assert ws.cases(a, offset=1)["items"] == []
    detail = ws.comparison_detail(a, b, "q1", 2, 1)
    assert detail["left"]["status"] == "passed" and detail["right"]["status"] == "failed"


def test_explicit_score_errors_are_not_hidden_as_semantic_false(database):
    ws.migrate()
    data = repeated()
    data["results"][1]["metrics"] = [{"id": "citation-v2", "dimension": "citation", "kind": "code",
                                        "evaluator_version": "fixture-v2", "status": "error", "reason": "Missing source metadata"}]
    identifier = add(data)
    detail = ws.detail(identifier, "q1", 2)
    assert detail["status"] == "failed"
    assert any(row["status"] == "error" for row in detail["metrics"])
    assert next(row for row in detail["metrics"] if row["id"] == "semantic_support")["status"] == "passed"


def test_job_recomputes_real_citation_audit_and_preserves_original_scores(database):
    ws.migrate()
    identifier = add()
    body = jobs.JobRequest(request_id="job-request-0001")
    created = jobs.create(identifier, body, "operator")
    assert jobs.create(identifier, body, "operator")["id"] == created["id"]
    with pytest.raises(ValueError):
        jobs.create(identifier, jobs.JobRequest(request_id="bad-command-0001", command="shell:echo"), "operator")
    job = jobs.claim()
    assert jobs.claim() is None
    jobs.execute(job)
    final = jobs.get(created["id"])
    assert final["status"] == "completed" and final["processed"] == 1
    assert final["failed_checks"] == 1 and final["model_calls"] == 0
    assert final["results"][0]["report"]["citation_audit"]["errors"][0]["code"] == "no_parseable_citation"
    assert ws.detail(identifier, "q1")["result"]["semantic_passed"] is False
    with pytest.raises(ValueError):
        jobs.resume(final["id"])


def test_job_cancellation_resume_skips_completed_attempts(database, monkeypatch):
    ws.migrate()
    data = repeated()
    data["results"].append(data["results"][1] | {"attempt_no": 3})
    identifier = add(data)
    created = jobs.create(identifier, jobs.JobRequest(request_id="cancel-resume-job"), "operator")
    calls = []
    original = jobs.audit_attempt

    def audit(case, result):
        calls.append(result["attempt_no"])
        if len(calls) == 1:
            jobs.cancel(created["id"])
        return original(case, result)

    monkeypatch.setattr(jobs, "audit_attempt", audit)
    jobs.execute(jobs.claim())
    assert jobs.get(created["id"])["status"] == "cancelled"
    assert jobs.get(created["id"])["processed"] == 1
    jobs.resume(created["id"])
    jobs.execute(jobs.claim())
    assert calls == [1, 2, 3]
    assert jobs.get(created["id"])["status"] == "completed"


def test_job_interruption_and_stale_worker_fencing(database):
    ws.migrate()
    identifier = add()
    created = jobs.create(identifier, jobs.JobRequest(request_id="interrupt-fence-job"), "operator")
    old = jobs.claim()
    with store.database() as connection:
        connection.execute("UPDATE eval_jobs SET heartbeat=? WHERE id=?", (time.time() - 120, created["id"]))
    assert jobs.get(created["id"])["status"] == "interrupted"
    jobs.resume(created["id"])
    current = jobs.claim()
    jobs.execute(old)
    assert jobs.get(created["id"])["processed"] == 0
    stop = threading.Event()
    stop.set()
    jobs.execute(current, stop)
    assert jobs.get(created["id"])["status"] == "interrupted"
    jobs.resume(created["id"])
    jobs.execute(jobs.claim())
    assert jobs.get(created["id"])["status"] == "completed"


def test_job_input_tampering_and_missing_answers_stay_failed(database):
    ws.migrate()
    data = repeated()
    identifier = add(data)
    created = jobs.create(identifier, jobs.JobRequest(request_id="missing-results-job"), "operator")
    jobs.execute(jobs.claim())
    assert jobs.get(created["id"])["status"] == "failed"
    assert jobs.get(created["id"])["errors"] == 1
    with store.database() as connection:
        connection.execute("UPDATE eval_attempts SET result='{}' WHERE run_id=? AND attempt_no=1", (identifier,))
    with pytest.raises(ValueError, match="Normalized"):
        jobs.resume(created["id"])


def decision(identifier, kind="ai_assisted", request_id="queue-decision-0001"):
    detail = ws.detail(identifier, "q1", 1)
    return {"request_id": request_id, "answer_sha256": detail["result"]["answer_sha256"],
            "rubric_id": detail["rubric_id"], "decision": "unsupported", "kind": kind, "reason": "Synthetic workflow contract; not real human adjudication"}


def test_queue_claim_conflict_idempotency_and_human_boundary(database):
    ws.migrate()
    identifier = add()
    queue = reviews.enqueue(identifier, "q1", 1)["id"]
    assert reviews.enqueue(identifier, "q1", 1)["id"] == queue
    claim = reviews.claim(queue, "reviewer-a")
    with pytest.raises(ValueError, match="another"):
        reviews.claim(queue, "reviewer-b")
    body = reviews.QueueDecision.model_validate(decision(identifier) | {"lock_token": claim["lock_token"]})
    result = reviews.complete(queue, body, "reviewer-a")
    assert result["status"] == "needs_adjudication" and not result["independent_human_gold"]
    assert reviews.complete(queue, body, "reviewer-a")["id"] == result["id"]
    with pytest.raises(ValueError, match="different"):
        reviews.claim(queue, "reviewer-a")
    second = reviews.claim(queue, "reviewer-b")
    adjudication = reviews.QueueDecision.model_validate(decision(identifier, "human", "adjudication-0001") |
                                                       {"lock_token": second["lock_token"], "action": "adjudicate"})
    assert reviews.complete(queue, adjudication, "reviewer-b")["status"] == "resolved"
    assert len(ws.detail(identifier, "q1")["reviews"]) == 2
    assert ws.detail(identifier, "q1")["status"] == "failed"


def test_expired_claim_rubric_binding_and_private_export(database):
    ws.migrate()
    identifier = add()
    queue = reviews.enqueue(identifier, "q1", 1)["id"]
    claim = reviews.claim(queue, "reviewer-a")
    with store.database() as connection:
        connection.execute("UPDATE eval_review_queue SET locked_until=0 WHERE id=?", (queue,))
    body = reviews.QueueDecision.model_validate(decision(identifier) | {"lock_token": claim["lock_token"]})
    with pytest.raises(ValueError, match="expired"):
        reviews.complete(queue, body, "reviewer-a")
    assert reviews.claim(queue, "reviewer-b")["assignee"] == "reviewer-b"
    bad = reviews.AttemptReview.model_validate(decision(identifier) | {"rubric_id": "wrong"})
    with pytest.raises(ValueError, match="rubric"):
        reviews.add(identifier, "q1", 1, bad, "reviewer-a")
    good = reviews.AttemptReview.model_validate(decision(identifier))
    reviews.add(identifier, "q1", 1, good, "reviewer-a")
    report = json.dumps(ws.export_report(identifier))
    for forbidden in (sample()["cases"][0]["question"], sample()["results"][0]["answer"], good.reason, "reviewer-a"):
        assert forbidden not in report


def test_expired_worker_cannot_restart_or_complete_without_resume(database):
    ws.migrate()
    identifier = add()
    created = jobs.create(identifier, jobs.JobRequest(request_id="expired-execution"), "operator")
    job = jobs.claim()
    with store.database() as connection:
        connection.execute("UPDATE eval_jobs SET heartbeat=0 WHERE id=?", (created["id"],))
    jobs.execute(job)
    assert jobs.get(created["id"])["status"] == "interrupted"
    assert jobs.get(created["id"])["processed"] == 0


def test_nonknowledge_or_scoped_refusal_does_not_invent_citation_failure():
    data = sample()
    result = data["results"][0] | {"contexts": []}
    case = data["cases"][0] | {"contract": {"target_locator": None}}
    report = jobs.audit_attempt(case, result)
    assert report["status"] == "passed"
    assert report["citation_audit"]["status"] == "not_applicable"
    assert report["semantic_acceptance"] is False


def test_human_disagreement_is_queued_for_other_account_without_changing_gold(database):
    ws.migrate()
    identifier = add()
    queue = reviews.enqueue(identifier, "q1", 1)["id"]
    claim = reviews.claim(queue, "synthetic-reviewer-a")
    body = reviews.QueueDecision.model_validate(decision(identifier, "human") |
                                                {"decision": "supported", "lock_token": claim["lock_token"]})
    assert reviews.complete(queue, body, "synthetic-reviewer-a")["status"] == "needs_adjudication"
    assert ws.detail(identifier, "q1")["result"]["semantic_passed"] is False


def test_auditor_version_change_cannot_resume_mixed_protocol(database, monkeypatch):
    ws.migrate()
    identifier = add()
    created = jobs.create(identifier, jobs.JobRequest(request_id="frozen-auditor-job"), "operator")
    jobs.cancel(created["id"])
    monkeypatch.setattr(jobs, "auditor_sha", lambda: "changed-code")
    with pytest.raises(ValueError, match="version"):
        jobs.resume(created["id"])


def test_v2_http_permissions_attempts_jobs_queue_and_exports(database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    import config
    import server
    from http_auth_helpers import authenticated_http, isolated_database
    from storage.models import Base

    ws.migrate()
    engine = create_engine(f"sqlite:///{tmp_path / 'http-auth.sqlite'}")
    Base.metadata.create_all(engine)
    with isolated_database(engine, ready=True):
        account = authenticated_http(server.app)
        client = account.client
        monkeypatch.setenv("CISP_EVAL_USER_IDS", str(account.user["id"]))
        imported = client.post("/api/evaluation/imports", json=repeated())
        assert imported.status_code == 200, imported.text
        identifier = imported.json()["id"]
        root = f"/api/evaluation/runs/{identifier}"
        assert client.get(root).json()["planned_attempts"] == 3
        assert client.get(root + "/cases/q1?attempt=2").json()["status"] == "passed"
        assert client.get(root + "/cases/q1?attempt=4").status_code == 404
        assert client.get("/api/evaluation/datasets").json()["items"]
        assert client.get("/api/evaluation/compare-detail", params={"left": identifier, "right": identifier, "case_id": "q1"}).json()["comparable"]
        assert client.post(root + "/jobs", json={"request_id": "http-job-0001"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        created = client.post(root + "/jobs", json={"request_id": "http-job-0001"})
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        assert client.post(f"/api/evaluation/jobs/{job_id}/cancel").json()["status"] == "cancelled"
        assert client.post(f"/api/evaluation/jobs/{job_id}/resume").json()["status"] == "queued"
        queue = client.post(root + "/cases/q1/attempts/2/queue").json()["id"]
        claim = client.post(f"/api/evaluation/review-queue/{queue}/claim").json()
        detail = client.get(root + "/cases/q1?attempt=2").json()
        review = {"request_id": "http-queue-review", "answer_sha256": detail["result"]["answer_sha256"],
                  "rubric_id": detail["rubric_id"], "lock_token": claim["lock_token"], "decision": "supported",
                  "kind": "ai_assisted", "reason": "Synthetic workflow only"}
        assert client.post(f"/api/evaluation/review-queue/{queue}/complete", json=review).json()["status"] == "needs_adjudication"
        second = authenticated_http(server.app)
        monkeypatch.setenv("CISP_EVAL_USER_IDS", str(account.user["id"]) + "," + str(second.user["id"]))
        assert client.post(f"/api/evaluation/review-queue/{queue}/claim").status_code == 422
        other_claim = second.client.post(f"/api/evaluation/review-queue/{queue}/claim").json()
        adjudication = review | {"request_id": "http-adjudication", "lock_token": other_claim["lock_token"],
                                 "action": "adjudicate", "kind": "human"}
        assert second.client.post(f"/api/evaluation/review-queue/{queue}/complete", json=adjudication).json()["status"] == "resolved"
        assert client.get(root + "/cases/q1?attempt=2").json()["status"] == "passed"
        assert client.get(root + "/export").json()["run"]["planned_attempts"] == 3
        assert client.get(root + "/export?format=markdown").status_code == 200
        assert client.get("/api/evaluation/jobs").headers["cache-control"] == "no-store"
        anonymous = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
        for path in ("/api/evaluation/jobs", "/api/evaluation/review-queue", "/api/evaluation/datasets"):
            assert anonymous.get(path).status_code == 401
            monkeypatch.setenv("CISP_EVAL_USER_IDS", "")
            assert client.get(path).status_code == 403
        anonymous.close()
        second.client.close()
        client.close()
    engine.dispose()
