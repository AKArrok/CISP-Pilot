"""Run repository-reproducible tests and reject unaudited CI skips."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOCAL_ASSETS = (
    ROOT / "data/questions.json",
    ROOT / "data/kb_chunks.json",
    ROOT / "data/knowledge_taxonomy.json",
    ROOT / "data/question_knowledge_tags.json",
    ROOT / "data/question_attributions.json",
)
EXPECTED_ASSET_SKIPS = {
    "tests/test_dense_taxonomy.py::test_development_set_is_reproducible_and_disjoint",
    "tests/test_dense_taxonomy.py::test_external_holdout_rejects_renamed_current_question",
    "tests/test_parsers.py::TestLocalData::test_kb_chunks_valid_and_cover_all_domains",
    "tests/test_parsers.py::TestLocalData::test_question_bank_nonempty",
    "tests/test_parsers.py::TestLocalData::test_questions_valid",
    "tests/test_profile_accuracy_eval.py::test_current_primary_tags_are_present_in_benchmark",
    "tests/test_profile_holdout_eval.py::test_holdout_is_stratified_clean_and_disjoint",
    "tests/test_profile_holdout_eval.py::test_holdout_scores_current_artifacts_without_fixture_predictions",
    "tests/test_reranker_backend.py::test_dense_can_be_intentionally_disabled_without_error",
    "tests/test_reranker_backend.py::test_dense_degradation_falls_back_to_bm25",
    "tests/test_retrieval_gold.py::test_gold_has_balanced_domain_coverage_and_resolvable_evidence",
    "tests/test_retrieval_gold.py::test_holdout_has_no_question_overlap_and_resolvable_evidence",
    "tests/test_table_chunking.py::TestRealPdfExtraction::test_known_table_page_extracts_with_caption",
}


class _SkipAudit:
    def __init__(self) -> None:
        self.nodeids: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped:
            self.nodeids.add(report.nodeid)


def main() -> int:
    leaked = [str(path.relative_to(ROOT)) for path in LOCAL_ASSETS if path.exists()]
    if leaked:
        print(
            "CI suite requires a checkout without local data assets; found: "
            + ", ".join(leaked),
            file=sys.stderr,
        )
        return 2

    audit = _SkipAudit()
    result = pytest.main(["-q"], plugins=[audit])
    if result != pytest.ExitCode.OK:
        return int(result)

    unexpected = sorted(audit.nodeids - EXPECTED_ASSET_SKIPS)
    missing = sorted(EXPECTED_ASSET_SKIPS - audit.nodeids)
    if unexpected or missing:
        if unexpected:
            print("Unexpected skipped tests:", *unexpected, sep="\n  ", file=sys.stderr)
        if missing:
            print("Expected asset skips not observed:", *missing, sep="\n  ", file=sys.stderr)
        return 1

    print(f"CI skip audit passed: {len(audit.nodeids)} expected asset-only skips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
