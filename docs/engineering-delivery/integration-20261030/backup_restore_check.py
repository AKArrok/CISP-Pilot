"""Offline backup/restore drill over this run's synthetic container database only."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parent
source = root / "docker-data-20261101/cisp_qa.db"
backup, restored = root / "synthetic-backup.db", root / "synthetic-restored.db"
assert not backup.exists() and not restored.exists(), "Never overwrite prior drill outputs"
before = hashlib.sha256(source.read_bytes()).hexdigest()
def snapshot(connection):
    names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    result = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        rows = sorted(repr(tuple(row)) for row in connection.execute("SELECT * FROM " + quoted))
        result[name] = {"rows": len(rows), "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest()}
    return result
with closing(sqlite3.connect("file:" + source.as_posix() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(backup)) as saved, closing(sqlite3.connect(restored)) as recovered:
    original.backup(saved)
    saved.backup(recovered)
    baseline = snapshot(original)
    assert baseline == snapshot(saved) == snapshot(recovered)
    assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list(recovered.execute("PRAGMA foreign_key_check"))
assert hashlib.sha256(source.read_bytes()).hexdigest() == before
(root / "backup-restore.json").write_text(json.dumps({"passed": True, "scope": "synthetic SQLite backup and restored copy; not default database or cloud recovery", "tables": baseline, "original_unchanged": True}, indent=2) + "\n", encoding="utf-8")
print("Synthetic SQLite backup and restore passed: all table row hashes, integrity, foreign keys and unchanged original.")
