import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    from storage import db

    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_SessionLocal", None)
    monkeypatch.setattr(db, "_backend", None)
    monkeypatch.setattr(db, "_schema_ready", False)
    monkeypatch.setattr(db.config, "MYSQL_URL_OVERRIDE", "")
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "fallback.sqlite"))
    monkeypatch.setenv("MYSQL_PASSWORD", "")
    yield db
    if db._engine is not None:
        db._engine.dispose()


class _ProbeEngine:
    def __init__(self, failure_stage=None, failure=None):
        self.failure_stage = failure_stage
        self.failure = failure
        self.disposals = 0
        self.connections_closed = 0

    def connect(self):
        if self.failure_stage == "connect":
            raise self.failure
        return self

    def __enter__(self):
        if self.failure_stage == "enter":
            raise self.failure
        return self

    def __exit__(self, *args):
        self.connections_closed += 1
        if self.failure_stage == "exit":
            raise self.failure

    def dispose(self):
        self.disposals += 1


@pytest.mark.parametrize("configuration", ["url", "password"])
@pytest.mark.parametrize("failure_stage", ["create", "connect", "enter", "exit", "session"])
def test_explicit_database_failure_leaves_no_fallback_or_cached_state_and_retries(
    isolated_db, monkeypatch, configuration, failure_stage
):
    db = isolated_db
    expected_url = "mysql+pymysql://tester:secret@db.example:3306/target?charset=utf8mb4"
    if configuration == "url":
        monkeypatch.setattr(db.config, "MYSQL_URL_OVERRIDE", expected_url)
    else:
        monkeypatch.setenv("MYSQL_USER", "tester")
        monkeypatch.setenv("MYSQL_PASSWORD", "secret")
        monkeypatch.setenv("MYSQL_HOST", "db.example")
        monkeypatch.setenv("MYSQL_PORT", "3306")
        monkeypatch.setenv("MYSQL_DB", "target")

    failure = RuntimeError("injected initialization failure")
    failed_engine = _ProbeEngine(failure_stage, failure)
    healthy_engine = _ProbeEngine()
    created_urls = []
    factories = []
    factory = object()

    def create_engine(url, **kwargs):
        created_urls.append(url)
        # A server-only URL would indicate automatic CREATE DATABASE; a SQLite
        # URL would indicate the forbidden fallback.
        assert url == expected_url
        if len(created_urls) == 1:
            if failure_stage == "create":
                raise failure
            return failed_engine
        return healthy_engine

    def sessionmaker(*, bind, expire_on_commit):
        factories.append(bind)
        assert expire_on_commit is False
        if failure_stage == "session" and bind is failed_engine:
            raise failure
        return factory

    monkeypatch.setattr(db, "create_engine", create_engine)
    monkeypatch.setattr(db, "sessionmaker", sessionmaker)

    with pytest.raises(RuntimeError) as caught:
        db.get_engine()
    assert caught.value is failure
    assert (db._engine, db._SessionLocal, db._backend) == (None, None, None)
    assert db._schema_ready is False
    assert not Path(db.config.DB_PATH).exists()
    assert created_urls == [expected_url]
    assert failed_engine.disposals == (0 if failure_stage == "create" else 1)

    assert db.get_engine() is healthy_engine
    assert (db._engine, db._SessionLocal, db._backend) == (healthy_engine, factory, "mysql")
    assert db.backend() == "mysql"
    assert created_urls == [expected_url, expected_url]
    assert factories[-1] is healthy_engine
    assert healthy_engine.connections_closed == 1
    assert healthy_engine.disposals == 0
    assert not Path(db.config.DB_PATH).exists()


def test_unconfigured_database_uses_default_sqlite(isolated_db):
    db = isolated_db
    engine = db.get_engine()
    with engine.connect():
        pass
    assert db.backend() == "sqlite"
    assert Path(db.config.DB_PATH).exists()


def test_unknown_explicit_url_scheme_never_creates_fallback(isolated_db, monkeypatch):
    from sqlalchemy.exc import NoSuchModuleError

    db = isolated_db
    monkeypatch.setattr(db.config, "MYSQL_URL_OVERRIDE", "invalidcisp://user:secret@host/target")
    with pytest.raises(NoSuchModuleError):
        db.get_engine()
    assert (db._engine, db._SessionLocal, db._backend) == (None, None, None)
    assert not Path(db.config.DB_PATH).exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_sqlite_session_factory_failure_disposes_candidate_and_can_retry(
    isolated_db, monkeypatch, tmp_path, explicit
):
    db = isolated_db
    if explicit:
        monkeypatch.setattr(
            db.config, "MYSQL_URL_OVERRIDE", f"sqlite:///{(tmp_path / 'explicit.sqlite').as_posix()}"
        )
    real_create_engine = db.create_engine
    real_sessionmaker = db.sessionmaker
    disposals = []

    def create_engine(*args, **kwargs):
        candidate = real_create_engine(*args, **kwargs)
        real_dispose = candidate.dispose

        def dispose():
            disposals.append(candidate)
            real_dispose()

        monkeypatch.setattr(candidate, "dispose", dispose)
        return candidate

    def fail_sessionmaker(**kwargs):
        raise RuntimeError("injected session factory failure")

    monkeypatch.setattr(db, "create_engine", create_engine)
    monkeypatch.setattr(db, "sessionmaker", fail_sessionmaker)
    with pytest.raises(RuntimeError, match="injected session factory failure"):
        db.get_engine()
    assert (db._engine, db._SessionLocal, db._backend) == (None, None, None)
    assert len(disposals) == 1
    assert not Path(db.config.DB_PATH).exists()

    monkeypatch.setattr(db, "sessionmaker", real_sessionmaker)
    assert db.get_engine() is not disposals[0]
    assert db.backend() == "sqlite"
    assert len(disposals) == 1


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
