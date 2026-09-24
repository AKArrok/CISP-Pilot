"""新评测分母、未知标签和边界回归。"""


def _row(qid, status, point=None, domain="信息安全管理", dense=True, tfidf=None, exact=None):
    routes = {
        "dense": {"available": dense, "candidates": ([{"knowledge_point_id": point}] if dense and point else [])},
        "tfidf": {"available": True, "candidates": ([{"knowledge_point_id": tfidf}] if tfidf else [])},
        "exact": {"available": True, "candidates": ([{"knowledge_point_id": exact}] if exact else [])},
    }
    return {"question_id": qid, "attribution_status": status,
            "candidate_point_id": point, "candidate_domain": domain,
            "evidence": {"routes": routes}}


def test_metrics_use_all_questions_for_coverage_but_known_labels_for_error():
    from data_ingest.attribution_eval import evaluate_attributions

    rows = [
        _row("q1", "confirmed", "p1"),
        _row("q2", "unverified"),
        _row("q3", "probable", "wrong"),
    ]
    report = evaluate_attributions(
        rows,
        gold=[{"question_id": "q1", "acceptable_knowledge_points": ["p1"]},
              {"question_id": "q3", "acceptable_knowledge_points": ["p3"]}],
        minimum_slice_size=2,
    )

    assert report["rejection_rate"] == 1 / 3
    assert report["accepted_coverage"] == 2 / 3
    assert report["observed_misattribution_rate"] == 0.5
    assert report["known_label_sample_size"] == 2
    assert report["confirmed_precision"] == 1.0
    assert report["probable_precision"] == 0.0


def test_metrics_preserve_zero_denominators_and_insufficient_status():
    from data_ingest.attribution_eval import evaluate_attributions

    report = evaluate_attributions([], minimum_slice_size=5)
    assert report["rejection_rate"] is None
    assert report["accepted_coverage"] is None
    assert report["observed_misattribution_rate"] is None
    assert report["observed_domain_misattribution_rate"] is None
    assert report["candidate_domain_accuracy"] is None
    assert report["joint_observed_misattribution_rate"] is None
    assert report["confirmed_domain_accuracy"] is None
    assert report["probable_joint_precision"] is None
    assert report["calibration_resolution_rate"] is None
    assert report["rejection_rate_detail"]["status"] == "insufficient"


def test_user_correction_is_the_only_label_for_observed_misattribution():
    from data_ingest.attribution_eval import evaluate_attributions

    row = _row("q-corrected", "confirmed", "p-wrong")
    report = evaluate_attributions(
        [row], user_corrections={"q-corrected": ["p-right"]}, minimum_slice_size=1,
    )
    assert report["known_label_sample_size"] == 1
    assert report["observed_misattribution_rate"] == 1.0


def test_existing_knowledge_tag_fixture_names_are_accepted_as_gold():
    from data_ingest.attribution_eval import evaluate_attributions

    row = {
        "question_id": "q-fixture", "attribution_status": "probable",
        "candidate_point_id": "kp-risk", "candidate": {
            "knowledge_point_id": "kp-risk", "name": "风险处理",
        },
        "evidence": {"routes": {}},
    }
    report = evaluate_attributions(
        [row],
        gold={"knowledge_tag_cases": [{
            "question_id": "q-fixture", "acceptable": ["风险处理"],
            "domain": "原始域", "expected_domain": "审校域",
        }]},
        minimum_slice_size=1,
    )

    assert report["known_label_sample_size"] == 1
    assert report["probable_precision"] == 1.0
    assert "审校域" in report["domain_slices"]


def test_calibration_success_means_resolution_not_answer_rate():
    from data_ingest.attribution_eval import evaluate_attributions

    report = evaluate_attributions(
        [],
        calibration_hypotheses=[
            {"status": "confirmed", "answered_evidence": [{"correct": False}, {"correct": False}],
             "opportunities": 2},
            {"status": "pending", "answered_evidence": [{"correct": True}],
             "opportunities": 2},
        ],
    )
    assert report["calibration_resolution_rate"] == 0.5
    assert report["calibration"]["entered"] == 2
    assert report["calibration"]["outcomes"] == {"confirmed": 1, "pending": 1}


def test_inconclusive_calibration_is_visible_but_not_counted_as_resolved():
    from data_ingest.attribution_eval import evaluate_attributions

    report = evaluate_attributions(
        [],
        calibration_hypotheses=[
            {"status": "confirmed", "answered_evidence": [], "entered": True},
            {"status": "inconclusive", "answered_evidence": [], "entered": True},
        ],
    )

    assert report["calibration_resolution_rate"] == 0.5
    assert report["calibration"] == {
        "entered": 2,
        "resolved_within_opportunities": 1,
        "outcomes": {"confirmed": 1, "inconclusive": 1},
    }


def test_calibration_denominator_uses_actual_presentations():
    from data_ingest.attribution_eval import evaluate_attributions

    resolved = evaluate_attributions(
        [],
        calibration_hypotheses=[
            {"status": "inconclusive", "presentation_count": 0, "evidence": []},
            {"status": "confirmed", "presentation_count": 2,
             "answered_evidence": [{"correct": False}, {"correct": False}],
             "opportunities": 2},
        ],
    )
    unresolved = evaluate_attributions(
        [],
        calibration_hypotheses=[
            {"status": "confirmed", "presentation_count": 0, "evidence": []},
            {"status": "pending", "presentation_count": 1,
             "answered_evidence": [{"correct": True}], "opportunities": 2},
            {"status": "resolved", "presentation_count": 1,
             "answered_evidence": [{"correct": True}], "opportunities": 2},
        ],
    )

    assert resolved["calibration_resolution_rate"] == 1.0
    assert resolved["calibration"] == {
        "entered": 1,
        "resolved_within_opportunities": 1,
        "outcomes": {"confirmed": 1, "inconclusive": 1},
    }
    assert unresolved["calibration_resolution_rate"] == 0.0
    assert unresolved["calibration"] == {
        "entered": 2,
        "resolved_within_opportunities": 0,
        "outcomes": {"confirmed": 1, "pending": 1, "resolved": 1},
    }


def test_report_cli_records_input_hashes_and_generation_time(tmp_path, monkeypatch):
    import hashlib
    import json
    from pathlib import Path
    import runpy
    import sys

    results_path = tmp_path / "results.json"
    gold_path = tmp_path / "gold.json"
    output_path = tmp_path / "report.json"
    results_path.write_text(
        json.dumps([_row("q1", "confirmed", "p1")]), encoding="utf-8"
    )
    gold_path.write_text(
        json.dumps([{"question_id": "q1", "acceptable_knowledge_points": ["p1"]}]),
        encoding="utf-8",
    )
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_attribution.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script_path),
            "--results", str(results_path),
            "--gold", str(gold_path),
            "--output", str(output_path),
        ],
    )

    runpy.run_path(str(script_path), run_name="__main__")
    report = json.loads(output_path.read_text(encoding="utf-8"))
    provenance = report["provenance"]

    assert provenance["generated_at_utc"].endswith("Z")
    assert provenance["hash_algorithm"] == "sha256"
    result_source = provenance["inputs"]["results"]
    assert result_source["path"].endswith("results.json")
    assert not Path(result_source["path"]).is_absolute()
    assert result_source["sha256"] == hashlib.sha256(results_path.read_bytes()).hexdigest()
    assert provenance["inputs"]["gold"]["sha256"] == hashlib.sha256(
        gold_path.read_bytes()
    ).hexdigest()
    assert provenance["inputs"]["corrections"] is None
    assert report["external_holdout"]["status"] == "optional_blocked"

def test_report_cli_refuses_to_overwrite_any_input(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import runpy
    import sys

    import pytest

    results_path = tmp_path / "results.json"
    gold_path = tmp_path / "gold.json"
    results_path.write_text(json.dumps([_row("q1", "confirmed", "p1")]), encoding="utf-8")
    gold_path.write_text(
        json.dumps([{"question_id": "q1", "acceptable_knowledge_points": ["p1"]}]),
        encoding="utf-8",
    )
    original_results = results_path.read_bytes()
    original_gold = gold_path.read_bytes()
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_attribution.py"

    for output_path in (results_path, gold_path):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                str(script_path),
                "--results", str(results_path),
                "--gold", str(gold_path),
                "--output", str(output_path),
            ],
        )
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(script_path), run_name="__main__")
        assert error.value.code == 2
        assert results_path.read_bytes() == original_results
        assert gold_path.read_bytes() == original_gold
def test_domain_and_joint_misattribution_are_visible_when_point_matches():
    from data_ingest.attribution_eval import evaluate_attributions

    row = {
        "question_id": "q-point-right-domain-wrong",
        "attribution_status": "confirmed",
        "candidate_point_id": "kp-right",
        "candidate_domain": "predicted-domain",
        "candidate": {"knowledge_point_id": "kp-right", "name": "correct-point", "domain": "predicted-domain"},
        "evidence": {"routes": {}},
    }
    missing_domain_row = {
        "question_id": "q-domain-missing",
        "attribution_status": "confirmed",
        "candidate_point_id": "kp-right",
        "candidate": {"knowledge_point_id": "kp-right", "name": "correct-point"},
        "evidence": {"routes": {}},
    }
    wrong_point_row = {
        "question_id": "q-domain-right-point-wrong",
        "attribution_status": "confirmed",
        "candidate_point_id": "kp-wrong",
        "candidate_domain": "reviewed-domain",
        "candidate": {"knowledge_point_id": "kp-wrong", "name": "wrong-point"},
        "evidence": {"routes": {}},
    }
    rows = [row, missing_domain_row, wrong_point_row]
    report = evaluate_attributions(
        rows,
        gold=[
            {
                "question_id": item["question_id"],
                "acceptable_knowledge_points": ["kp-right"],
                "expected_domain": "reviewed-domain",
            }
            for item in rows[:2]
        ] + [{
            "question_id": wrong_point_row["question_id"],
            "acceptable_knowledge_points": ["kp-right"],
            "expected_domain": "reviewed-domain",
        }],
        minimum_slice_size=1,
    )

    # Preserve point-only precision while surfacing missing/wrong domains and either-way errors.
    assert report["observed_misattribution_rate"] == 1 / 3
    assert report["confirmed_precision"] == 2 / 3
    assert report["observed_domain_misattribution_rate"] == 2 / 3
    assert report["candidate_domain_accuracy"] == 1 / 3
    assert report["joint_observed_misattribution_rate"] == 1.0
    assert report["confirmed_domain_accuracy"] == 1 / 3
    assert report["confirmed_joint_precision"] == 0.0


def test_empty_gold_row_is_not_scorable_but_domain_only_gold_is():
    from data_ingest.attribution_eval import evaluate_attributions

    rows = [
        _row("q-empty-gold", "confirmed", "p1"),
        _row("q-domain-only", "confirmed", "candidate-point", domain="reviewed-domain"),
    ]
    report = evaluate_attributions(
        rows,
        gold=[
            {"question_id": "q-empty-gold", "acceptable_knowledge_points": []},
            {"question_id": "q-domain-only", "expected_domain": "reviewed-domain"},
        ],
        minimum_slice_size=1,
    )

    assert report["known_label_sample_size"] == 1
    assert report["observed_misattribution_rate"] == 0.0
    assert report["confirmed_precision"] == 1.0
    assert report["candidate_domain_accuracy"] == 1.0
    assert report["confirmed_domain_accuracy"] == 1.0
    assert report["confirmed_joint_precision"] == 1.0

    empty_only = evaluate_attributions(
        [rows[0]],
        gold=[{"question_id": "q-empty-gold", "acceptable_knowledge_points": []}],
        minimum_slice_size=1,
    )
    assert empty_only["known_label_sample_size"] == 0
    assert empty_only["observed_misattribution_rate"] is None
    assert empty_only["confirmed_precision"] is None
