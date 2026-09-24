"""受控地解析知识库引用到原始课件文件。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import config

_SUPPORTED_SUFFIXES = {".pdf", ".pptx"}


@lru_cache(maxsize=1)
def _allowed_sources() -> frozenset[str]:
    """只允许访问已进入知识库的资料，不能把接口变成任意文件读取。"""
    import json

    try:
        with open(config.KB_CHUNKS_PATH, encoding="utf-8") as fp:
            return frozenset(chunk["source"] for chunk in json.load(fp))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return frozenset()


@lru_cache(maxsize=128)
def find_document(source: str) -> Path | None:
    """按切片时记录的文件名（不含扩展名）查找原始资料。"""
    if source not in _allowed_sources():
        return None

    for root in (Path(config.COURSE_DIR), Path(config.EXAM_DIR)):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES and path.stem == source:
                return path
    return None
