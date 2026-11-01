"""Source locations shared by answer prompts and citation audits."""
from __future__ import annotations

import re


def source_location(source: dict) -> str:
    if source.get("page") is not None:
        return f"第{source['page']}页"
    locator = source.get("locator", "")
    if isinstance(locator, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", locator):
        return f"定位【{locator}】"
    raise ValueError("Source requires an original page or a structured locator")


def source_preview(source: dict) -> dict:
    source_location(source)  # Reject missing locations before emitting a broken source.
    result = {"source": source["source"], "page": source.get("page"),
              "domain": source["domain"], "text": source["text"][:120]}
    if source.get("page") is None:
        result.update(locator=source["locator"], source_url=source.get("source_url"))
    return result
