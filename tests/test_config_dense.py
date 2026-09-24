import pytest

import config


def test_validate_allows_bm25_only_without_embedding_credentials(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "test-llm-key")
    monkeypatch.setattr(config, "ENABLE_DENSE_RETRIEVAL", False)
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "dashscope")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_EMBEDDING_BASE_URL", "")

    config.validate()


def test_validate_requires_embedding_credentials_when_dense_is_enabled(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "test-llm-key")
    monkeypatch.setattr(config, "ENABLE_DENSE_RETRIEVAL", True)
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "dashscope")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_EMBEDDING_BASE_URL", "")

    with pytest.raises(EnvironmentError, match="DASHSCOPE_API_KEY"):
        config.validate()
