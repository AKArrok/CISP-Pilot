# Frozen NIST development evaluation

This packet contains 32 AI-authored development questions over four public NIST controls, plus the unchanged AT-2 regression question. It is not an independent human holdout. Exact normalized novelty was checked only against the two historical packets described in manifest.json; broader and semantic overlap remain unverified.

From the repository root, install evaluation dependencies from requirements.txt in this directory, then run:

```powershell
python -m scripts.eval_nist_batch --output evaluation-output-offline
python -m pytest tests/test_nist_batch.py -q
```

Offline mode compares English source text, existing bilingual search text, and source-plus-alias indexing. Reference evidence is never added as a separate indexed answer. No retrieval threshold is lowered. Target-control retrieval measures topic location, not whether the retrieved source entails a claim. The experiment searches four isolated controls, not the production corpus.

The live mode requires explicit authorization bound to manifest.json and a unique dispatch.lock. The included authorization was consumed by the archived run; do not remove the lock or reuse that approval. New runs require a new versioned packet and authorization. Credentials remain in local environment variables or .env, never this packet. Only the approved DeepSeek HTTPS endpoint is allowed, with Flash, thinking disabled, zero SDK retries, and 1024 maximum output tokens. Unknown usage, truncated output or input/budget overrun stops the run. Execution completion is not semantic acceptance.

Sources: pinned official OSCAL catalog, source receipt and CC0 license included. Chinese search metadata is AI-authored and is not original evidence. Prompts are frozen for reproducibility; judge-followup.txt records a proposed clarification that has not been calibrated and does not overwrite original scores.

Results and raw provider receipts are under docs/engineering-delivery/nist-batch-20261030-live. They contain only public-source requests and model responses. Read the delivery report for limitations and pending source reviews.
