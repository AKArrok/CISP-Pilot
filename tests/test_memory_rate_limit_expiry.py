"""The Redis fallback must retain a counter's original fixed-window expiry."""
from storage import cache


def test_memory_counter_recovers_after_its_original_window(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["now"])
    monkeypatch.setattr(cache, "_mem_store", {})
    monkeypatch.setattr(cache, "_client", cache._MemClient())
    assert cache.rate_limit("window", 2, 60) == (True, 1)
    clock["now"] = 130.0
    assert cache.rate_limit("window", 2, 60) == (True, 2)
    assert cache.rate_limit("window", 2, 60) == (False, 3)
    clock["now"] = 161.0
    assert cache.rate_limit("window", 2, 60) == (True, 1)


def test_memory_increment_keeps_persistent_keys_persistent(monkeypatch):
    monkeypatch.setattr(cache, "_mem_store", {})
    client = cache._MemClient()
    assert client.incr("persistent") == 1
    assert client.incr("persistent") == 2
    assert client._data["persistent"][0] is None
