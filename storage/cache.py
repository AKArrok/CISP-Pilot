"""Redis 缓存层 — 仿 Dify 的 redis_client 单例包装。

- 连接不可达时自动降级为进程内 dict 缓存（功能可用、无跨进程共享）
- 提供 JSON 读写（带 TTL）与固定窗口限流
"""
from __future__ import annotations

import json
import logging
import threading
import time

import config

logger = logging.getLogger(__name__)

_client = None
_live = False  # True = 真 Redis；False = 内存降级
_mem_store: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()
_counter_lock = threading.Lock()


def _init():
    global _client, _live
    with _lock:
        if _client is not None:
            return _client
        try:
            import redis as redis_lib
            client = redis_lib.Redis.from_url(
                config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2,
                decode_responses=False,
            )
            client.ping()
            _client, _live = client, True
            logger.info("缓存层: Redis (%s)", config.REDIS_URL)
        except Exception:
            _client, _live = _MemClient(), False
            logger.warning("Redis 不可达，缓存降级为进程内存（功能不受影响，仅无跨进程共享）")
    return _client


class _MemClient:
    """进程内降级实现，接口对齐 redis-py 的子集。"""

    def __init__(self):
        self._data = _mem_store

    def _purge(self) -> None:
        now = time.time()
        expired = [k for k, (exp, _) in self._data.items() if exp and exp < now]
        for k in expired:
            self._data.pop(k, None)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._purge()
        self._data[key] = ((time.time() + ex) if ex else None, value)

    def get(self, key: str) -> bytes | None:
        self._purge()
        item = self._data.get(key)
        return item[1] if item else None

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._data.pop(k, None)

    def incr(self, key: str) -> int:
        self._purge()
        exp, val = self._data.get(key, (None, b"0"))
        n = int(val) + 1
        self._data[key] = (exp, str(n).encode())
        return n

    def expire(self, key: str, seconds: int) -> None:
        if key in self._data:
            exp, val = self._data[key]
            self._data[key] = (time.time() + seconds, val)

    def ping(self) -> bool:
        return True


# ── 对外接口 ────────────────────────────────────────────────────────────

def get_json(key: str):
    """读 JSON 值，缺失/过期/损坏返回 None。"""
    raw = _init().get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def set_json(key: str, value, ttl: int | None = None) -> None:
    _init().set(key, json.dumps(value, ensure_ascii=False).encode(), ex=ttl)


def delete_keys(*keys: str) -> None:
    if keys:
        _init().delete(*keys)


def is_live() -> bool:
    _init()
    return _live


def rate_limit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """固定窗口限流：返回 (是否放行, 窗口内已计数)。"""
    client = _init()
    if isinstance(client, _MemClient):
        with _counter_lock:
            n = client.incr(key)
            if n == 1:
                client.expire(key, window_seconds)
    else:
        # One Redis operation keeps increment and initial expiry atomic across workers.
        n = int(client.eval(
            "local n = redis.call('INCR', KEYS[1]); "
            "if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end; return n",
            1, key, window_seconds,
        ))
    return n <= limit, n
