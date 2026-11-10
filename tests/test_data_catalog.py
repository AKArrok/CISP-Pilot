"""Catalog contracts: observed data is not invented lineage or independent quality evidence."""
import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from scripts.package_data_catalog import package
from storage import data_catalog as data, evaluation_store as store, evaluation_workspace as evaluation
from test_evaluation_platform import sample


def knowledge():
    return {"schema_version": 1, "namespace": "synthetic-knowledge", "label": "frozen synthetic corpus", "purpose": "knowledge",
            "visibility": "synthetic", "artifacts": {"chunks": "a" * 64}, "manifest": {"chunk_count": 2, "vector_count": 2},
            "processor": {"historical_execution": "unknown"}, "sources": [{"name": "synthetic", "locator": "", "sha256": None}],
            "chunks": [{"id": "c1", "source": "synthetic", "page": 1, "text": sample()["results"][0]["contexts"][0]["text"]},
                       {"id": "c2", "source": "synthetic", "page": 2, "text": "Another original synthetic definition for pagination."}],
            "vector_ids": ["c1", "c2"]}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "platform.sqlite3"
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(path))
    evaluation.migrate()
    data.migrate()
    return path


def imported(bundle=None):
    return data.import_snapshot(data.Snapshot.model_validate(bundle or knowledge()), "synthetic-actor")["id"]


def run(bundle=None):
    return store.import_bundle(evaluation.parse_bundle(bundle or sample()), "synthetic-actor")["id"]


def proposal(snapshot, baseline):
    return data.Improvement(request_id="synthetic-proposal", run_id=baseline, case_id="q1", attempt=1, snapshot_id=snapshot,
                            category="unknown", hypothesis="Investigate source scope", evidence="Frozen answer and source",
                            acceptance="Same-case retest and full regression without degrading other cases", chunk_ids=["c1"])


def test_explicit_migration_required_and_original_import_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(path))
    evaluation.migrate()
    baseline = run()
    raw = store.get_run(baseline)["payload"]
    with pytest.raises(ValueError, match="explicitly"):
        imported()
    assert data.migrate()["upgraded"]
    assert not data.migrate()["upgraded"]
    imported()
    assert store.get_run(baseline)["payload"] == raw


@pytest.mark.parametrize("mutation", ["duplicate", "unknown_source", "answer_set", "bad_hash", "purpose", "extra", "parent_type", "path_id"])
def test_invalid_snapshot_rejected(mutation):
    d = knowledge()
    if mutation == "duplicate":
        d["chunks"].append(copy.deepcopy(d["chunks"][0]))
    elif mutation == "unknown_source":
        d["chunks"][0]["source"] = "unregistered"
    elif mutation == "answer_set":
        d["chunks"] = [{"question": "test question", "answer": "test answer"}]
    elif mutation == "bad_hash":
        d["artifacts"]["chunks"] = "not-a-hash"
    elif mutation == "purpose":
        d["purpose"] = "gold_answers"
    elif mutation == "parent_type":
        d["chunks"][0]["parent_id"] = {"bad": "type"}
    elif mutation == "path_id":
        d["chunks"][0]["id"] = "../outside"
    else:
        d["execute"] = "arbitrary command"
    with pytest.raises((ValidationError, ValueError)):
        data.Snapshot.model_validate(d)


def test_versions_paging_quality_and_inline_cisp_parent(database):
    identifier = imported()
    assert not data.import_snapshot(data.Snapshot.model_validate(knowledge()), "second")["created"]
    assert data.chunks(identifier, limit=1)["total"] == 2
    assert data.chunks(identifier, offset=1, limit=1)["items"][0]["id"] == "c2"
    assert data.chunks(identifier, query="no-match")["total"] == 0
    bundle = knowledge()
    bundle["label"] = "new version"
    bundle["vector_ids"].reverse()
    bundle["manifest"]["chunk_count"] = 999
    bundle["chunks"][0].update(parent_id="p1", child_text="child", chunking_strategy="structured_v1")
    other = imported(bundle)
    assert identifier != other and len(data.snapshots()) == 2
    checks = {row["id"]: row["status"] for row in data.snapshot(other)["quality"]["checks"]}
    assert checks["index_alignment"] == checks["manifest_chunk_count"] == "failed"
    assert checks["parent_evidence"] == "passed"
    assert checks["original_source_fingerprints"] == "unknown"
    assert data.chunk(other, "c1")["parent"]["text"] == bundle["chunks"][0]["text"]
    assert data.chunk(other, "c1")["original_file_status"] == "unknown"
    bundle["chunks"][0].pop("child_text")
    assert next(c for c in data.quality(bundle)["checks"] if c["id"] == "parent_evidence")["status"] == "failed"


def test_lineage_requires_explicit_historical_hash_and_handles_ambiguity(database):
    sid = imported()
    baseline = run()
    result = data.lineage(sid, baseline, "q1")
    assert result["version_binding"] == "historical_version_unknown"
    assert result["matches"][0]["candidates"][0]["match"] == "exact_text"
    assert result["matches"][0]["candidates"][0]["verified"] is False
    bundle = sample()
    bundle["artifacts"]["knowledge:synthetic-knowledge"] = "a" * 64
    bound = data.lineage(sid, run(bundle), "q1")
    assert bound["version_binding"] == "hash_bound" and bound["matches"][0]["candidates"][0]["verified"]
    bundle["artifacts"]["knowledge:synthetic-knowledge"] = "b" * 64
    assert data.lineage(sid, run(bundle), "q1")["version_binding"] == "hash_mismatch"
    chunks = knowledge()
    chunks["chunks"][1]["text"] = chunks["chunks"][0]["text"]
    result = data.lineage(imported(chunks), baseline, "q1")
    assert result["matches"][0]["ambiguous"] and result["matches"][0]["candidate_count"] == 2


def test_excerpt_and_changed_id_never_silently_verify(database):
    bundle = knowledge()
    sentence = "This is a sufficiently long synthetic excerpt for match tests."
    bundle["chunks"][0]["text"] = sentence + " More text after the recorded preview."
    sid = imported(bundle)
    b = sample()
    b["results"][0]["contexts"][0].update(text=sentence, page=1)
    b["artifacts"]["knowledge:synthetic-knowledge"] = "a" * 64
    match = data.lineage(sid, run(b), "q1")["matches"][0]["candidates"][0]
    assert match["match"] == "excerpt_candidate" and not match["verified"]
    b["results"][0]["contexts"][0].update(text="changed", id="c1")
    match = data.lineage(sid, run(b), "q1")["matches"][0]["candidates"][0]
    assert match["match"] == "id_candidate" and not match["verified"]
    b["results"] = []
    assert not data.lineage(sid, run(b), "q1")["matches"]


def test_failure_dimensions_and_sources_do_not_double_count(database):
    b = sample()
    b["results"][0]["contexts"] *= 2
    rid = run(b)
    result = data.analysis(rid, offset=0, limit=1)
    assert result["attempt_counts"] == [{"status": "failed", "count": 1}]
    assert result["sources"] == [{"source": "synthetic", "status": "failed", "attempts": 1}]
    assert result["total"] == 1 and data.analysis(rid, offset=1)["failures"] == []
    assert any(row["dimension"] == "semantic" and row["status"] == "failed" for row in result["metrics"])


def test_append_only_improvement_retest_and_comparability(database):
    sid, baseline = imported(), run()
    body = proposal(sid, baseline)
    created = data.create_item(body, "actor")
    assert data.create_item(body, "actor")["id"] == created["id"]
    with pytest.raises(ValueError, match="changed"):
        data.create_item(body.model_copy(update={"evidence": "different"}), "actor")
    assert created["payload"]["frozen"]["answer_sha256"] == sample()["results"][0]["answer_sha256"]
    candidate = sample()
    candidate["name"] = "candidate"
    candidate["results"][0]["semantic_passed"] = True
    cid = run(candidate)
    event = data.Event(request_id="synthetic-retest", revision=0, action="retest", note="Recorded synthetic comparison", candidate_run=cid)
    updated = data.append_event(created["id"], event, "actor")
    assert updated["events"][0]["payload"]["result"]["outcome"] == "fixed"
    assert updated["events"][0]["payload"]["result"]["independent_human_gold"] is False
    assert data.append_event(created["id"], event, "actor")["revision"] == 1
    assert store.get_run(baseline)["payload"]["results"][0]["semantic_passed"] is False
    with pytest.raises(ValueError, match="Revision"):
        data.append_event(created["id"], data.Event(request_id="stale-revision", revision=0, action="note", note="stale"), "other")
    candidate["criteria"] = "Changed criterion"
    cid = run(candidate)
    event = data.Event(request_id="different-rubric", revision=1, action="retest", note="Not comparable", candidate_run=cid)
    assert data.append_event(created["id"], event, "actor")["events"][1]["payload"]["result"]["outcome"] == "incomparable"
    assert data.items(limit=1)["total"] == 1


def test_concurrent_append_fences_old_revision(database):
    identifier = data.create_item(proposal(imported(), run()), "actor")["id"]

    def append(index):
        try:
            data.append_event(identifier, data.Event(request_id=f"concurrent-{index}", revision=0, action="note", note=str(index)), str(index))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(append, (1, 2))) == [False, True]
    assert data.item(identifier)["revision"] == 1


def test_package_observes_explicit_files_and_rejects_answer_sets(tmp_path):
    chunks = tmp_path / "chunks.json"
    chunks.write_text(json.dumps(knowledge()["chunks"]), encoding="utf-8")
    original = tmp_path / "original.txt"
    original.write_text("Synthetic source original", encoding="utf-8")
    mapping = tmp_path / "sources.json"
    mapping.write_text(json.dumps({"synthetic": "original.txt"}), encoding="utf-8")
    before = hashlib.sha256(chunks.read_bytes()).hexdigest()
    result = package(chunks, source_map_path=mapping)
    assert result.artifacts["chunks"] == before
    assert result.sources[0].sha256 == hashlib.sha256(original.read_bytes()).hexdigest()
    assert result.visibility == "private" and result.processor["historical_execution"] == "unknown"
    assert hashlib.sha256(chunks.read_bytes()).hexdigest() == before
    chunks.write_text(json.dumps([{"question": "question", "answer": "answer"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="knowledge"):
        package(chunks)


def test_protects_business_and_unrelated_database(tmp_path, monkeypatch):
    import config

    monkeypatch.setenv("CISP_EVAL_DB_PATH", config.DB_PATH)
    with pytest.raises(ValueError, match="separate"):
        data.migrate()
    other = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    before = other.read_bytes()
    monkeypatch.setenv("CISP_EVAL_DB_PATH", str(other))
    with pytest.raises(ValueError, match="unrelated"):
        data.migrate()
    assert before == other.read_bytes()


def test_http_auth_csrf_catalog_and_ledger(database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    import server
    from http_auth_helpers import authenticated_http, isolated_database
    from storage.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'auth.sqlite'}")
    Base.metadata.create_all(engine)
    with isolated_database(engine, ready=True):
        account = authenticated_http(server.app)
        client = account.client
        monkeypatch.setenv("CISP_EVAL_USER_IDS", str(account.user["id"]))
        assert client.get("/api/data/capabilities").json()["schema_version"] == 1
        assert client.post("/api/data/imports", json=knowledge(), headers={"X-CSRF-Token": "wrong"}).status_code == 403
        response = client.post("/api/data/imports", json=knowledge())
        assert response.status_code == 200, response.text
        sid = response.json()["id"]
        rid = run()
        assert client.get(f"/api/data/snapshots/{sid}/chunks?limit=1").json()["total"] == 2
        assert client.get(f"/api/data/snapshots/{sid}/chunks/c1").json()["source"]["sha256"] is None
        assert client.get(f"/api/data/analysis/{rid}").json()["total"] == 1
        assert client.get("/api/data/lineage", params={"snapshot": sid, "run": rid, "case": "q1"}).json()["version_binding"] == "historical_version_unknown"
        created = client.post("/api/data/improvements", json=proposal(sid, rid).model_dump())
        assert created.status_code == 200, created.text
        identifier = created.json()["id"]
        event = data.Event(request_id="http-data-event", revision=0, action="note", note="Synthetic HTTP check")
        assert client.post(f"/api/data/improvements/{identifier}/events", json=event.model_dump()).json()["revision"] == 1
        assert client.get("/api/data/improvements").json()["total"] == 1
        assert client.get("/api/data/snapshots/unknown").status_code == 404
        assert client.get(f"/api/data/snapshots/{sid}/chunks?offset=-1").status_code == 422
        assert TestClient(server.app).get("/api/data/snapshots").status_code == 401
        monkeypatch.setenv("CISP_EVAL_USER_IDS", "other-account")
        assert client.get("/api/data/snapshots").status_code == 403
