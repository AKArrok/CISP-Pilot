"""Source locations shared by answer prompts and citation audits."""
from __future__ import annotations

import re


class CitationLineFormatter:
    """Put the literal citation heading on its own line, including across SSE chunks."""
    marker = "【出处】"

    def __init__(self):
        self.pending = ""
        self.last = ""

    def feed(self, text: str) -> str:
        output = []
        for char in text:
            self.pending += char
            while self.pending and not self.marker.startswith(self.pending):
                self.last = self.pending[0]
                output.append(self.last)
                self.pending = self.pending[1:]
            if self.pending == self.marker:
                if self.last and self.last != "\n":
                    output.append("\n")
                output.append(self.marker)
                self.last = self.marker[-1]
                self.pending = ""
        return "".join(output)

    def finish(self) -> str:
        remaining, self.pending = self.pending, ""
        if remaining:
            self.last = remaining[-1]
        return remaining


def format_citation_lines(text: str) -> str:
    formatter = CitationLineFormatter()
    return formatter.feed(text) + formatter.finish()


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
