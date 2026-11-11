"""Source locations shared by answer prompts and citation audits."""
from __future__ import annotations

import re

CITATION_WARNING = "【出处未核实】回答未提供可核验的完整来源，请结合检索资料核对。"


def render_selected_citations(answer: str, contexts: list[dict]) -> str:
    """Render metadata only for explicit, valid model-selected references."""
    if "【出处】" in answer:
        return answer
    refs = list(dict.fromkeys(re.findall(r"\[资料(\d+)\]", answer)))
    if not refs or any(not 1 <= int(ref) <= len(contexts) for ref in refs):
        return answer
    try:
        rendered = [f"[资料{ref}]《{contexts[int(ref)-1]['source']}》{source_location(contexts[int(ref)-1])}"
                    for ref in refs]
    except (KeyError, TypeError, ValueError):
        return answer
    return answer + "\n\n【出处】" + "；".join(rendered)


def citation_is_verifiable(answer: str, contexts: list[dict]) -> bool:
    """Check citation identities only; this does not establish semantic support."""
    pattern = r"\[资料(\d+)\]《(.*?)》(第\d+页|定位【[A-Za-z][A-Za-z0-9_.:-]*】)"
    matches = list(re.finditer(pattern, answer))
    refs = re.findall(r"\[资料(\d+)\]", answer)
    if not matches or not set(refs).issubset({match[1] for match in matches}):
        return False
    for match in matches:
        index, title, location = int(match[1]) - 1, match[2], match[3]
        if not 0 <= index < len(contexts):
            return False
        source = contexts[index]
        try:
            if title != source["source"] or location != source_location(source):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


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
