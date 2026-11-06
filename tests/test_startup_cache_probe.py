import pytest

import server
from retrieval.hybrid import HybridRetriever


@pytest.mark.parametrize("redis_live", [False, True])
def test_ready_startup_resolves_cache_before_first_user_request(monkeypatch, redis_live):
    calls = []
    monkeypatch.setattr(server, "_readiness_payload", lambda: ({"status": "ready"}, True))
    monkeypatch.setattr(server.config, "EMBEDDING_BACKEND", "dashscope")
    monkeypatch.setattr(server.cache, "is_live", lambda: calls.append("cache") or redis_live)
    monkeypatch.setattr(HybridRetriever, "get", lambda: calls.append("retriever"))
    monkeypatch.setattr("llms.get_embeddings", lambda: pytest.fail("Remote embedding must remain lazy"))
    server.warmup()
    assert calls == ["cache", "retriever"]


def test_unready_startup_does_not_probe_cache_or_load_models(monkeypatch):
    monkeypatch.setattr(server, "_readiness_payload", lambda: ({"status": "migration_required"}, False))

    def forbidden():
        raise AssertionError("Unready startup must not initialize dependencies")

    monkeypatch.setattr(server.cache, "is_live", forbidden)
    monkeypatch.setattr("llms.get_embeddings", forbidden)
    monkeypatch.setattr(HybridRetriever, "get", forbidden)
    server.warmup()
