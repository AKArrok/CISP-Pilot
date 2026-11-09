"""Actual HTTP limiter counters, with password service replaced only to count admissions."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from storage import auth, cache
from storage.http_auth import router


def test_username_limit_across_ips_normalization_and_expiry(monkeypatch):
    memory = cache._MemClient()
    memory._data = {}
    monkeypatch.setattr(cache, "_client", memory)
    clock = {"now": 1000.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["now"])
    monkeypatch.setattr(config, "CISP_PUBLIC_ORIGIN", "http://testserver")
    admissions = []
    monkeypatch.setattr(auth, "login", lambda name, password: admissions.append(name) or None)
    app = FastAPI()
    app.include_router(router)

    def attempt(index, username):
        with TestClient(app, client=(f"192.0.2.{index}", 1234)) as client:
            return client.post("/api/auth/login", json={"username": username, "password": "synthetic-password"},
                               headers={"Origin": "http://testserver"}).status_code

    assert [attempt(index, "Alice" if index % 2 else " alice ") for index in range(1, 11)] == [401] * 10
    assert len(admissions) == 10
    assert attempt(11, "ALICE") == 429
    assert len(admissions) == 10
    assert attempt(12, "different-user") == 401
    clock["now"] += 301
    assert attempt(13, "ALICE") == 401
    assert len(admissions) == 12


def test_forwarded_header_cannot_bypass_actual_client_ip_limit(monkeypatch):
    memory = cache._MemClient()
    memory._data = {}
    monkeypatch.setattr(cache, "_client", memory)
    monkeypatch.setattr(config, "CISP_PUBLIC_ORIGIN", "http://testserver")
    admissions = []
    monkeypatch.setattr(auth, "login", lambda name, password: admissions.append(name) or None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for index in range(31):
            response = client.post("/api/auth/login", json={"username": f"synthetic-{index}", "password": "synthetic-password"},
                                   headers={"Origin": "http://testserver", "X-Forwarded-For": f"192.0.2.{index}"})
            assert response.status_code == (401 if index < 30 else 429)
    assert len(admissions) == 30
