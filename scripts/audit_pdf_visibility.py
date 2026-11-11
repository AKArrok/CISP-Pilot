"""Read-only heuristic for PDF text covered by later opaque vector drawings.

This flags candidates for visual review; bounding boxes do not prove visibility.
It never changes or exports the source document or extracted text.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf


def covered_text_spans(page, threshold: float = 0.8) -> list[dict]:
    """Report text spans whose glyph centers overlap later opaque drawing bounds."""
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    covers = [drawing for drawing in page.get_drawings()
              if drawing.get("fill") is not None and drawing.get("fill_opacity", 0) >= 0.99]
    findings = []
    for span in page.get_texttrace():
        chars = [char for char in span["chars"] if not chr(char[0]).isspace()]
        if not chars:
            continue
        count = 0
        for char in chars:
            rect = pymupdf.Rect(char[3])
            center = (rect.tl + rect.br) / 2
            if any(drawing["seqno"] > span["seqno"] and drawing["rect"].contains(center)
                   for drawing in covers):
                count += 1
        ratio = count / len(chars)
        if ratio >= threshold:
            findings.append({"sequence": span["seqno"], "characters": len(chars),
                             "covered_fraction": round(ratio, 4)})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, help="1-based physical PDF page; default all pages")
    args = parser.parse_args()
    with pymupdf.open(args.pdf) as document:
        if args.page is not None and not 1 <= args.page <= len(document):
            parser.error("page must be within the PDF")
        indexes = [args.page - 1] if args.page is not None else range(len(document))
        pages = [{"physical_page": i + 1, "suspected_spans": len(covered_text_spans(document[i]))}
                 for i in indexes]
    print(json.dumps({"method": "later opaque vector drawing bounding boxes; visual review required",
                      "pages": pages}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
