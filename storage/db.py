"""引擎与会话工厂 — DATABASE_URL 驱动（仿 Dify 的 12-factor 配置方式）。

- 主后端: mysql+pymysql（utf8mb4），库不存在时自动 CREATE DATABASE
- 降级:   MySQL 连接失败时回退 SQLite（沿用原 data/cisp_qa.db），仅告警不崩溃
"""
from __future__ import annotations

import logging
import os
import threading
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateColumn
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

import config

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None
_backend = None  # "mysql" | "sqlite"
_lock = threading.RLock()  # RLock: create_all -> get_engine -> _init 同线程重入

SCHEMA_HEAD = "f1a2b3c4d5e6"


def _mysql_url() -> str:
    user = os.getenv("MYSQL_USER", "root")
    password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    name = os.getenv("MYSQL_DB", "cisp_qa")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


def _create_database_if_missing(url: str) -> None:
    """连接不含库名的 URL，CREATE DATABASE IF NOT EXISTS。"""
    base = url.rsplit("/", 1)[0]
    name = url.rsplit("/", 1)[1].split("?")[0]
    server_engine = create_engine(base + "/", pool_pre_ping=True)
    with server_engine.connect() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{name}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
        conn.commit()
    server_engine.dispose()


def _init() -> tuple:
    global _engine, _SessionLocal, _backend
    with _lock:
        if _engine is not None:
            return _engine, _SessionLocal, _backend

        explicit_url = config.MYSQL_URL_OVERRIDE.strip()
        if explicit_url.startswith("sqlite:"):
            _engine = create_engine(
                explicit_url, connect_args={"check_same_thread": False}
            )
            _backend = "sqlite"
            logger.info("存储后端: SQLite（DATABASE_URL）")
            _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
            return _engine, _SessionLocal, _backend

        mysql_url = explicit_url or _mysql_url()
        if os.getenv("MYSQL_PASSWORD") or explicit_url:
            try:
                _create_database_if_missing(mysql_url)
                _engine = create_engine(
                    mysql_url, pool_pre_ping=True, poolclass=QueuePool,
                    pool_size=5, pool_recycle=1800,
                )
                with _engine.connect():
                    pass  # 真连一次才算数
                _backend = "mysql"
                logger.info("存储后端: MySQL (%s)", mysql_url.rsplit("@", 1)[1])
                _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
                return _engine, _SessionLocal, _backend
            except Exception:
                logger.warning(
                    "MySQL 连接失败，降级 SQLite: %s",
                    bool(os.getenv("MYSQL_PASSWORD", "")),
                )

        sqlite_url = f"sqlite:///{config.DB_PATH}"
        _engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
        _backend = "sqlite"
        logger.info("存储后端: SQLite (%s)", config.DB_PATH)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine, _SessionLocal, _backend


def backend() -> str:
    return _init()[2]


def get_engine():
    return _init()[0]


def session() -> Session:
    return _init()[1]()

def schema_status() -> dict:
    """Read-only migration status for readiness and deployment audits."""
    engine = get_engine()
    revisions: list[str] = []
    if "alembic_version" in inspect(engine).get_table_names():
        with engine.connect() as connection:
            revisions = sorted(
                row[0]
                for row in connection.execute(
                    text("select version_num from alembic_version")
                )
            )
    return {
        "database_backend": backend(),
        "expected_revision": SCHEMA_HEAD,
        "current_revisions": revisions,
        "migration_current": revisions == [SCHEMA_HEAD],
    }


def create_all() -> None:
    """建表（幂等）。并发首调加锁串行化——MySQL 上并发 CREATE/反射会互等元数据锁。"""
    global _schema_ready
    with _lock:
        if _schema_ready:
            return
        from storage.models import Base
        engine = get_engine()
        Base.metadata.create_all(engine)
        _ensure_additive_columns(engine, Base)
        _schema_ready = True


_schema_ready = False


def _ensure_additive_columns(engine, base) -> None:
    """补齐 ``create_all`` 无法修改的列，覆盖半迁移旧库边界。

    Alembic 仍是正式迁移入口；这里仅做启动时幂等兼容，避免旧 SQLite
    数据库在迁移尚未执行时因 ORM 查询新列而直接不可用。
    """
    table_name = "learner_attempts"
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    table = base.metadata.tables[table_name]
    missing = [column for column in table.columns if column.name not in existing]
    preparer = engine.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    with engine.begin() as connection:
        for column in missing:
            definition = str(CreateColumn(column).compile(dialect=engine.dialect))
            connection.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN {definition}"))
        # ``create_all`` does not create indexes for a table that already
        # exists.  Keep the compatibility path aligned with the ORM metadata.
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def override_engine(engine) -> None:
    """测试钩子：替换全局引擎/会话工厂为指定实例。"""
    global _engine, _SessionLocal, _backend, _schema_ready
    with _lock:
        _engine = engine
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        _backend = "sqlite"
        _schema_ready = False
