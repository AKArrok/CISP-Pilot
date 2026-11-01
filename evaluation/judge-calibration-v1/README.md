# Seen-answer judge calibration

Eight AI-labelled calibration cases exercise parameter meaning, mandatory actions, valid structured citations, wrong locators, empty answers and justified refusal. These cases are development data, not independent human gold. Labels and expected score ranges are not sent to the model.

v1 compares the original and candidate judge on the same answers. v2 keeps the exact same case bytes and tests a candidate that emits evidence checks. v3 retains those cases and clarifies literal quote copying; it is prepared only, with no authorization and no model execution.

Run offline audits from the repository root:

```powershell
python -m scripts.audit_judge_calibration --packet evaluation/judge-calibration-v1 --run docs/engineering-delivery/judge-calibration-20261030-r1 --output judge-audit-v1.json
python -m scripts.audit_judge_calibration --packet evaluation/judge-calibration-v2 --run docs/engineering-delivery/judge-calibration-20261030-r2 --output judge-audit-v2.json
python -m pytest tests/test_judge_calibration_contract.py -q
```

An audit exits 1 when calibration is incomplete or invalid; this is expected for the archived runs. Do not retry model calls because the audit exits nonzero. Both consumed dispatch locks must remain. v2 stopped after a provider 503: four responses observed, one attempted call with unknown usage, three not started. One observed response reconstructs a source quote, so only three meet the strict combined score/evidence check. Model scores and raw outputs remain unchanged.

Only public NIST excerpts and saved model answers are included. Source license and receipt are in ../nist-development-v1. No API credentials are included. Live calls require a fresh explicit authorization; unknown usage stops subsequent dispatches.
