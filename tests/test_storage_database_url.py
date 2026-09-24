import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_storage_database_url_sqlite_does_not_fall_back_to_cisp_db_path(tmp_path):
    target_path = tmp_path / "database-url.sqlite"
    decoy_path = tmp_path / "cisp-db-path.sqlite"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{target_path.as_posix()}"
    env["CISP_DB_PATH"] = str(decoy_path)
    env["MYSQL_PASSWORD"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from storage import db; "
                "db.create_all(); "
                "assert db.backend() == 'sqlite'"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_path.exists()
    assert not decoy_path.exists()
