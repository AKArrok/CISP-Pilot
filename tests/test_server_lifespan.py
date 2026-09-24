from fastapi.testclient import TestClient

import server


def test_lifespan_runs_warmup_once(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "warmup", lambda: calls.append("warmup"))

    with TestClient(server.app) as client:
        assert client.get("/api/domains").status_code == 200

    assert calls == ["warmup"]
