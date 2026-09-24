"""输出保守题目归因开发集报告。

用法：
  python scripts/evaluate_attribution.py --results data/question_attributions.json
  python scripts/evaluate_attribution.py --results ... --gold tests/fixtures/... --output data/attribution_report.json

默认只做 development 口径；外部 holdout 仍须先通过现有双人盲审工具，缺失时
保持 blocked，不被本脚本当作运行时准确率。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from data_ingest.attribution_eval import evaluate_attributions


def _source_record(path: str, raw: bytes) -> dict:
    absolute_path = os.path.abspath(path)
    try:
        relative_path = os.path.relpath(absolute_path, config.PROJECT_ROOT)
    except ValueError:
        relative_path = os.path.basename(absolute_path)
    if os.path.isabs(relative_path) or relative_path == os.pardir or relative_path.startswith(
        os.pardir + os.sep
    ):
        relative_path = os.path.basename(absolute_path)
    return {
        "path": relative_path.replace(os.sep, "/"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _read_source(path: str | None, default):
    if not path:
        return default, None
    with open(path, "rb") as fp:
        raw = fp.read()
    return json.loads(raw.decode("utf-8")), _source_record(path, raw)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=config.QUESTION_ATTRIBUTIONS_PATH)
    parser.add_argument("--gold", help="可选开发集金标；未知题不计入 precision 分母")
    parser.add_argument("--corrections", help="可选用户明确纠正标签")
    parser.add_argument("--calibration", help="可选校准假设/机会记录")
    parser.add_argument("--output", default=os.path.join(config.DATA_DIR, "attribution_report.json"))
    parser.add_argument("--dataset-role", default="development")
    args = parser.parse_args()
    output_identity = os.path.normcase(os.path.realpath(args.output))
    for option, source in (
        ("results", args.results),
        ("gold", args.gold),
        ("corrections", args.corrections),
        ("calibration", args.calibration),
    ):
        if source and os.path.normcase(os.path.realpath(source)) == output_identity:
            parser.error(f"--output must not overwrite --{option} input")
    results, results_source = _read_source(args.results, [])
    gold, gold_source = _read_source(args.gold, None)
    corrections, corrections_source = _read_source(args.corrections, None)
    calibration, calibration_source = _read_source(args.calibration, [])
    report = evaluate_attributions(
        results,
        gold=gold,
        user_corrections=corrections,
        calibration_hypotheses=calibration,
        dataset_role=args.dataset_role,
    )
    report["provenance"] = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "hash_algorithm": "sha256",
        "inputs": {
            "results": results_source,
            "gold": gold_source,
            "corrections": corrections_source,
            "calibration": calibration_source,
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report -> {args.output}")


if __name__ == "__main__":
    main()
