"""Audit internal consistency of a saved AI verdict/issues review; no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def review_outcome(review: dict) -> dict:
    """Return a conservative reporting status, not an independent semantic judgment."""
    if (not isinstance(review, dict) or not isinstance(review.get("verdict"), str)
            or review["verdict"] not in {"pass", "fail", "uncertain"}):
        return {"status": "error", "inconsistent": False, "reason": "invalid_verdict"}
    issues = review.get("issues")
    if not isinstance(issues, list) or any(not isinstance(i, dict) or
            not isinstance(i.get("status"), str) or i["status"] not in {"supported", "unsupported", "uncertain"} for i in issues):
        return {"status": "error", "inconsistent": False, "reason": "invalid_issues"}
    states = {i["status"] for i in issues}
    verdict = review["verdict"]
    if "unsupported" in states:
        return {"status": "failed", "inconsistent": verdict == "pass", "reason": "unsupported_claim"}
    if "uncertain" in states:
        return {"status": "unknown", "inconsistent": verdict == "pass", "reason": "uncertain_claim"}
    if verdict == "fail":
        return {"status": "unknown", "inconsistent": True, "reason": "failure_without_failed_claim"}
    return {"status": "passed" if verdict == "pass" else "unknown",
            "inconsistent": False, "reason": "reported_" + verdict}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.review.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        value = None
    print(json.dumps(review_outcome(value), ensure_ascii=False))


if __name__ == "__main__":
    main()
