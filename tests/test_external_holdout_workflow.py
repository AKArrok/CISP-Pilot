"""外部 holdout 盲审工作流的结构回归；合成题不是泛化评测集。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def _source() -> dict:
    return {
        "schema_version": 1,
        "source": {
            "kind": "external", "name": "workflow-test", "url": "https://example.test",
            "license_or_permission": "test-only", "permission_evidence_url": "https://example.test/license",
            "content_sha256": "test-content-hash", "acquired_at": "2026-09-14",
        },
        "questions": [{
            "id": f"external-workflow-{index}",
            "stem": f"仅用于验证盲审管道的独立测试题干 {index} alpha beta gamma",
            "options": {"A": "正确", "B": "错误"}, "answer": "A", "analysis": "",
        } for index in range(100)],
    }


def _taxonomy() -> list[dict]:
    return [{
        "id": "business-continuity-plan",
        "domain": "业务连续性",
        "name": "业务连续性计划",
        "status": "active",
    }]


def _complete(packet: dict, reviewer_id: str) -> dict:
    packet["reviewer_id"] = reviewer_id
    packet["reviewed_at"] = "2026-09-14T01:00:00+08:00"
    for row in packet["cases"]:
        row["reviewed_domain"] = "业务连续性"
        row["acceptable_knowledge_points"] = ["业务连续性计划"]
    return packet


def test_external_double_blind_packets_can_be_frozen_after_adjudication(tmp_path):
    from copy import deepcopy

    import pytest

    from scripts.assemble_external_holdout import assemble
    from scripts.create_external_blind_review_packets import create_packets
    from tests.eval_profile_external_holdout import validate_external_holdout

    source = _source()
    packets = create_packets(source, taxonomy=_taxonomy())
    left = _complete(packets["reviewer_a"], "human-a")
    right = _complete(packets["reviewer_b"], "human-b")
    assert [row["id"] for row in left["cases"]] != [row["id"] for row in right["cases"]]

    paths = {}
    for name, payload in {
        "source": source,
        "reviewer_a": left,
        "reviewer_b": right,
    }.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    adjudication_path = tmp_path / "adjudication.json"
    script = Path(__file__).resolve().parents[1] / "scripts/create_external_adjudication_packet.py"
    generated = subprocess.run(
        [
            sys.executable, str(script),
            "--source", str(paths["source"]),
            "--review-a", str(paths["reviewer_a"]),
            "--review-b", str(paths["reviewer_b"]),
            "--output", str(adjudication_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert generated.returncode == 0, generated.stderr
    adjudication = json.loads(adjudication_path.read_text("utf-8"))
    adjudication["adjudicator_id"] = "human-adjudicator"
    adjudication["adjudicated_at"] = "2026-09-14T02:00:00+08:00"
    for decision in adjudication["cases"]:
        decision["adjudicated_domain"] = "业务连续性"
        decision["acceptable"] = ["业务连续性计划"]
        decision["note"] = "agreement"

    fixture = assemble(source, left, right, adjudication)
    integrity = validate_external_holdout(
        fixture, current_questions=[], taxonomy=_taxonomy(),
    )
    assert integrity["ready"] is True
    assert integrity["checks"]["review_label_overlap"] == 1.0
    assert integrity["checks"]["review_provenance_bound"] is True
    assert integrity["checks"]["source_questions_hash_matches"] is True

    changed_source_hash_fixture = deepcopy(fixture)
    changed_source_hash_fixture["adjudication"]["source_questions_sha256"] = "0" * 64
    changed_source_hash_integrity = validate_external_holdout(
        changed_source_hash_fixture, current_questions=[], taxonomy=_taxonomy(),
    )
    assert changed_source_hash_integrity["ready"] is False
    assert changed_source_hash_integrity["checks"]["source_questions_hash_matches"] is False

    tampered_fixture = deepcopy(fixture)
    tampered_fixture["reviewers"][0]["review_packet_sha256"] = "0" * 64
    tampered_integrity = validate_external_holdout(
        tampered_fixture, current_questions=[], taxonomy=_taxonomy(),
    )
    assert tampered_integrity["ready"] is False
    assert tampered_integrity["checks"]["review_provenance_bound"] is False

    changed_source = deepcopy(source)
    changed_source["questions"][0]["stem"] += " changed"
    with pytest.raises(ValueError, match="哈希"):
        assemble(changed_source, left, right, adjudication)

    changed_right = deepcopy(right)
    changed_right["cases"][0]["acceptable_knowledge_points"] = ["edited-after-adjudication"]
    with pytest.raises(ValueError, match="审校包哈希"):
        assemble(source, left, changed_right, adjudication)

    changed_adjudication = deepcopy(adjudication)
    changed_adjudication["source_questions_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="原始题源哈希"):
        assemble(source, left, right, changed_adjudication)

    changed_adjudication = deepcopy(adjudication)
    changed_adjudication["reviewer_ids"] = ["someone-else", "human-b"]
    with pytest.raises(ValueError, match="审校者"):
        assemble(source, left, right, changed_adjudication)

def test_external_packet_rejects_labels_leaked_into_source():
    from scripts.create_external_blind_review_packets import create_packets

    source = _source()
    source["questions"][0]["model_prediction"] = "不应出现"
    try:
        create_packets(source)
    except ValueError as exc:
        assert "预测字段" in str(exc)
    else:
        raise AssertionError("含模型预测的外部题源必须被拒绝")

    for field in (
        "reviewed_domain", "acceptable_knowledge_points", "review_note",
        "reviewed_domains", "adjudication_note",
    ):
        source = _source()
        source["questions"][0][field] = "不应出现"
        try:
            create_packets(source, taxonomy=_taxonomy())
        except ValueError as exc:
            assert "标签或预测字段" in str(exc)
        else:
            raise AssertionError(f"原始题源泄漏 {field} 时必须被拒绝")

def test_external_adjudication_script_runs_directly(tmp_path):
    from scripts.create_external_blind_review_packets import create_packets

    source = _source()
    packets = create_packets(source, taxonomy=_taxonomy())
    paths = {}
    for name, payload in {
        "source": source,
        "reviewer_a": packets["reviewer_a"],
        "reviewer_b": packets["reviewer_b"],
    }.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/create_external_adjudication_packet.py"
    result = subprocess.run(
        [sys.executable, str(script), "--source", str(paths["source"]),
         "--review-a", str(paths["reviewer_a"]), "--review-b", str(paths["reviewer_b"]),
         "--output", str(tmp_path / "adjudication.json")],
        capture_output=True,
    )

    assert result.returncode == 1
    assert b"reviewer_id/reviewed_at" in result.stderr
