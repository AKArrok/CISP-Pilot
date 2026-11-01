"""Explicit real login and thread ownership for isolated HTTP tests."""

from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

import config
from storage import auth, db, repos
from storage.models import Thread


@contextmanager
def isolated_database(engine, *, ready=False):
    """Use a temporary DB; opt normal HTTP tests into current schema/assets."""
    if ready:
        with engine.begin() as connection:
            connection.execute(text(
                "create table alembic_version (version_num varchar(32) not null)"
            ))
            connection.execute(
                text("insert into alembic_version (version_num) values (:revision)"),
                {"revision": db.SCHEMA_HEAD},
            )
    previous = (db._engine, db._SessionLocal, db._backend, db._schema_ready)
    db.override_engine(engine)
    try:
        assets = patch("data_ingest.manifest.validate_index", return_value=None) if ready else nullcontext()
        with assets:
            yield
    finally:
        db._engine, db._SessionLocal, db._backend, db._schema_ready = previous


@dataclass
class AuthenticatedHttp:
    client: TestClient
    user: dict

    def own_thread(self, thread_id: str | None = None) -> str:
        if thread_id is None:
            return repos.create_thread(self.user["id"])["id"]
        # Tests with pre-existing hard-coded evidence need the same thread ID.
        with db.session() as session:
            session.add(Thread(
                id=thread_id, owner_user_id=self.user["id"],
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            session.commit()
        return thread_id


def authenticated_http(app) -> AuthenticatedHttp:
    username = f"test-{uuid4().hex}"
    password = "correct-horse-battery-staple"
    user = auth.register(username, password)
    login = auth.login(username, password)
    assert login is not None
    origin = config.CISP_PUBLIC_ORIGIN
    client = TestClient(app, base_url=origin)
    client.cookies.set("cisp_session", login["session_token"])
    client.headers.update({"Origin": origin, "X-CSRF-Token": login["csrf_token"]})
    return AuthenticatedHttp(client, user)
