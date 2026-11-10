"""Evaluation workspace contracts: isolated storage, provenance, permissions and comparison."""
import copy
import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

import config
import server
from http_auth_helpers import authenticated_http, isolated_database
from scripts.package_evaluation import package
from storage import evaluation_store as store
from storage.models import Base


def sample():
    answer = "Synthetic answer: source does not specify the requested extra condition."
    return {"schema_version": 1, "name": "synthetic baseline", "dataset_name": "synthetic contract fixture",
            "role": "synthetic", "visibility": "synthetic", "criteria": "synthetic evidence contract v1",
            "config": {"model": "none"}, "artifacts": {"fixture": "a" * 64},
            "cases": [{"id": "q1", "question": "Synthetic question?", "contract": {"expected": "source scope"}}],
            "results": [{"id": "q1", "answer": answer, "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                         "contexts": [{"source": "synthetic", "text": "Synthetic definition."}],
                         "checks": {"citation": True}, "semantic_passed": False,
                         "semantic_reviewer": "synthetic fixture; not a human reviewer", "semantic_reason": "Synthetic failure"}],
            "known_cost_usd": 0.0, "cost_unknown": False}


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "platform.sqlite3"
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(path))
    return path


def add(data=None):
    return store.import_bundle(store.Bundle.model_validate(data or sample()), "fixture-actor")["id"]


def test_import_is_idempotent_and_missing_evidence_is_not_pass(isolated_store):
    bundle = store.Bundle.model_validate(sample())
    first = store.import_bundle(bundle, "one")
    assert first["created"]
    assert store.import_bundle(bundle, "two") == {"id": first["id"], "created": False}
    assert store.list_runs()[0]["counts"] == {"passed": 0, "failed": 1, "unknown": 0}
    data = sample()
    data["results"] = []
    identifier = add(data)
    assert store.cases(identifier)["items"][0]["status"] == "unknown"
    assert store.offline_check(identifier, "one")["payload"]["status"] == "failed"
    data = sample()
    data["results"][0]["semantic_passed"] = None
    assert store.cases(add(data))["items"][0]["status"] == "unknown"


@pytest.mark.parametrize("mutation", ["duplicate_case", "duplicate_result", "unknown_result", "hash", "reviewer", "cost", "extra"])
def test_reject_invalid_inputs(mutation):
    data = sample()
    if mutation == "duplicate_case":
        data["cases"] *= 2
    elif mutation == "duplicate_result":
        data["results"] *= 2
    elif mutation == "unknown_result":
        data["results"][0]["id"] = "unknown"
    elif mutation == "hash":
        data["results"][0]["answer_sha256"] = "b" * 64
    elif mutation == "reviewer":
        data["results"][0]["semantic_reviewer"] = ""
    elif mutation == "cost":
        data["known_cost_usd"] = float("nan")
    else:
        data["path"] = "../private"
    with pytest.raises(ValidationError):
        store.Bundle.model_validate(data)


def test_comparison_preserves_contracts_and_denominators(isolated_store):
    baseline = add()
    candidate = sample()
    candidate["name"] = "candidate"
    candidate["config"]["model"] = "other"
    candidate["results"][0]["semantic_passed"] = True
    candidate_id = add(candidate)
    result = store.compare(baseline, candidate_id)
    assert result["counts"]["fixed"] == 1 and result["config_changed"]
    assert store.compare(candidate_id, baseline)["counts"]["regressed"] == 1
    for change in ("question", "contract", "criteria", "role"):
        data = copy.deepcopy(candidate)
        if change in {"question", "contract"}:
            data["cases"][0][change] = "changed" if change == "question" else {"expected": "different"}
        else:
            data[change] = "changed" if change == "criteria" else "development"
        assert store.compare(baseline, add(data))["counts"]["incomparable"] == 1
    data = sample()
    data["results"] = []
    assert store.compare(baseline, add(data))["counts"]["insufficient_evidence"] == 1
    data["cases"][0]["id"] = "q2"
    counts = store.compare(baseline, add(data))["counts"]
    assert counts["added"] == counts["removed"] == 1


def test_append_only_reviews_idempotent_and_bound_to_answer(isolated_store):
    identifier = add()
    data = {"request_id": "request-0001", "answer_sha256": sample()["results"][0]["answer_sha256"],
            "decision": "needs_evidence", "reason": "Need a source locator", "kind": "human"}
    review = store.Review.model_validate(data)
    first = store.add_review(identifier, "q1", review, "one")
    assert store.add_review(identifier, "q1", review, "one") == first
    with pytest.raises(ValueError):
        store.add_review(identifier, "q1", store.Review.model_validate(data | {"reason": "Changed"}), "one")
    with pytest.raises(ValueError):
        store.add_review(identifier, "q1", store.Review.model_validate(data | {"answer_sha256": "0" * 64}), "one")
    detail = store.detail(identifier, "q1")
    assert len(detail["reviews"]) == 1 and detail["status"] == "failed"
    assert detail["result"]["semantic_passed"] is False


def test_export_redacts_private_text_and_config(isolated_store):
    data = sample()
    data["visibility"] = "private"
    data["name"] = data["dataset_name"] = "private secret name"
    data["config"] = {"secret": "private secret config"}
    data["artifacts"] = {"private secret path": "f" * 64}
    identifier = add(data)
    report = json.dumps(store.export_report(identifier))
    assert "private secret" not in report
    assert data["results"][0]["answer"] not in report
    assert data["cases"][0]["question"] not in report
    assert "Synthetic definition" not in report
    assert "independent_human_gold" in report


def test_database_target_guards(isolated_store, tmp_path, monkeypatch):
    monkeypatch.setenv("CISP_EVAL_DB_PATH", config.DB_PATH)
    with pytest.raises(ValueError, match="separate"):
        store.list_runs()
    unrelated = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(unrelated) as connection:
        connection.execute("CREATE TABLE unrelated (id TEXT)")
    before = hashlib.sha256(unrelated.read_bytes()).hexdigest()
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(unrelated))
    with pytest.raises(ValueError, match="unrelated"):
        store.list_runs()
    assert hashlib.sha256(unrelated.read_bytes()).hexdigest() == before


def test_checks_are_idempotent_persistent_and_not_semantic_acceptance(isolated_store):
    identifier = add()
    first = store.offline_check(identifier, "one")
    assert first["payload"]["status"] == "completed"
    assert first["payload"]["semantic_acceptance"] is False
    assert first["payload"]["model_calls"] == 0
    assert store.offline_check(identifier, "two") == first
    assert store.list_runs()[0]["counts"]["failed"] == 1
    with sqlite3.connect(isolated_store) as connection:
        tampered = sample()
        tampered["name"] = "externally modified"
        connection.execute("UPDATE eval_runs SET payload=? WHERE id=?", (store.canonical(tampered), identifier))
    with pytest.raises(ValueError, match="immutable"):
        store.offline_check(identifier, "three")


def test_http_auth_csrf_import_filter_review_export(isolated_store, tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.sqlite'}")
    Base.metadata.create_all(engine)
    with isolated_database(engine, ready=True):
        account = authenticated_http(server.app)
        client = account.client
        anon = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
        assert anon.get("/api/evaluation/runs").status_code == 401
        assert client.get("/api/evaluation/runs").status_code == 403
        assert not isolated_store.exists()
        monkeypatch.setenv("CISP_EVAL_USER_IDS", str(account.user["id"]))
        assert client.post("/api/evaluation/imports", json=sample(), headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/evaluation/imports", json=sample(), headers={"Origin": "http://evil.invalid"}).status_code == 403
        assert client.post("/api/evaluation/imports", content="bad").status_code == 422
        assert client.post("/api/evaluation/imports", content=b" " * (8 * 1024 * 1024 + 1)).status_code == 413
        imported = client.post("/api/evaluation/imports", json=sample())
        assert imported.status_code == 200, imported.text
        identifier = imported.json()["id"]
        root = f"/api/evaluation/runs/{identifier}"
        assert client.get(root).headers["cache-control"] == "no-store"
        assert client.get(root + "/cases?status=failed&limit=1").json()["total"] == 1
        assert client.get(root + "/cases?offset=-1").status_code == 422
        assert client.get(root + "/cases?query=absent").json()["total"] == 0
        assert client.get(root + "/cases/missing").status_code == 404
        assert client.get("/api/evaluation/compare?left=missing&right=missing").status_code == 404
        review = {"request_id": "http-review-1", "answer_sha256": sample()["results"][0]["answer_sha256"],
                  "decision": "needs_evidence", "reason": "Browser source check", "kind": "ai_assisted"}
        assert client.post(root + "/cases/q1/reviews", json=review).status_code == 200
        assert len(client.get(root + "/cases/q1").json()["reviews"]) == 1
        assert client.post(root + "/checks").json()["payload"]["status"] == "completed"
        assert client.get(root + "/export").json()["run"]["counts"]["failed"] == 1
        assert client.get(root + "/export?format=markdown").text.startswith("# CISP")
        monkeypatch.setenv("CISP_EVAL_USER_IDS", "")
        for path in (root, root + "/cases", root + "/cases/q1", root + "/export"):
            assert client.get(path).status_code == 403
        assert client.post(root + "/checks").status_code == 403
        client.close()
        anon.close()
    engine.dispose()


def test_packager_binds_review_and_rejects_changed_frozen_input(tmp_path):
    packet, run = tmp_path / "packet", tmp_path / "run"
    packet.mkdir()
    run.mkdir()
    data = sample()
    files = {"cases.json": [{"id": "q1", "question": "Synthetic question?", "role": "synthetic"}],
             "chunks.json": [{"id": "c1", "source": "synthetic", "text": "Synthetic source"}]}
    for name, value in files.items():
        (packet / name).write_text(json.dumps(value), encoding="utf-8")
    frozen = {name: hashlib.sha256((packet / name).read_bytes()).hexdigest() for name in files}
    (packet / "manifest.json").write_text(json.dumps({"frozen_sha256": frozen, "acceptance": "synthetic"}), encoding="utf-8")
    result = data["results"][0]
    for name, value in {"results.json": [{"id": "q1", "question": "Synthetic question?", "events": [{"type": "token", "content": result["answer"]}]}],
                        "audit.json": {"checks": [{"id": "q1", "passed": True, "checks": {"test": True}}]},
                        "summary.json": {"known_cost_estimate_usd": 0},
                        "semantic-review.json": {"reviewer": "synthetic", "cases": [{"id": "q1", "source_supported": False,
                                                                                      "answer_sha256": result["answer_sha256"]}]}}.items():
        (run / name).write_text(json.dumps(value), encoding="utf-8")
    assert package(packet, run).results[0].semantic_passed is False
    enriched = package(packet, run, 2)
    assert enriched.schema_version == 2 and enriched.results[0].attempt_no == 1
    assert enriched.dataset_key == "public-nist-http"
    assert enriched.artifacts["knowledge:public-nist-http"] == frozen["chunks.json"]
    assert "knowledge:public-nist-http" not in package(packet, run).artifacts
    assert enriched.cases[0].contract["preceding_user_questions"] == []
    review_path = run / "semantic-review.json"
    review = json.loads(review_path.read_text())
    review["cases"][0]["answer_sha256"] = "0" * 64
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="bind"):
        package(packet, run)
    (packet / "cases.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen"):
        package(packet, run)
