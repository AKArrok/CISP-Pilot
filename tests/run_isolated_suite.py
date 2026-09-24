"""Run the complete pytest suite against a disposable, fully populated SQLite DB."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    ROOT / "data" / "questions.json",
    ROOT / "data" / "manifest.json",
    ROOT / "data" / "knowledge_taxonomy.json",
    ROOT / "data" / "question_knowledge_tags.json",
    ROOT / "data" / "question_attributions.json",
)


def _hash_artifacts() -> dict[Path, str]:
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in ARTIFACTS
    }


def _assert_artifacts_unchanged(before: dict[Path, str]) -> None:
    after = _hash_artifacts()
    changed = [str(path.relative_to(ROOT)) for path in ARTIFACTS if before[path] != after[path]]
    if changed:
        raise RuntimeError(
            "隔离测试不得改写构建产物: " + ", ".join(changed)
        )


def main() -> int:
    before = _hash_artifacts()
    with tempfile.TemporaryDirectory(prefix=".cisp-pytest-", dir=ROOT) as temp_dir:
        temp_root = Path(temp_dir)
        env = os.environ.copy()
        env["CISP_DB_PATH"] = str(temp_root / "suite.db")
        env["DATABASE_URL"] = ""
        env["MYSQL_PASSWORD"] = ""
        env["TEMP"] = str(temp_root)
        env["TMP"] = str(temp_root)

        subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/build_taxonomy.py",
                "--import-db",
                "--db-only",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
        )
        _assert_artifacts_unchanged(before)

        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-q",
                "--basetemp",
                str(temp_root / "pytest"),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        _assert_artifacts_unchanged(before)
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
