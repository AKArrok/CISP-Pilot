"""PDF 表格感知提取：find_tables 检测 → 质量过滤 → markdown 序列化 + 表题探测。

课件里的表格（等级保护定级矩阵、STRIDE、风险等级…）经 page.get_text() 平铺后
行列关系被打散，切片时还会被拆进不同 Child。这里把通过质量过滤的表格序列化成
markdown，使其成为带表题的独立检索单元；未通过过滤的"表格"（装饰框、跨页图形、
并排多表）保持平铺留在正文里，避免抽取丢字造成信息损失。
"""
from __future__ import annotations

import re

import pymupdf

import config

# Wingdings/Symbol 私有区项目符号（\uf06c 等）在平铺文本里是乱码
_PUA_GLYPH_RE = re.compile(r"[\ue000-\uf8ff]")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def clean_cell(text: str | None) -> str:
    """单元格规整：私有区符号转 •；含中文去全部空白（换行断词），英文仅折叠空白。"""
    if not text:
        return ""
    text = _PUA_GLYPH_RE.sub("•", text).replace("\u00a0", " ")
    if _has_cjk(text):
        return re.sub(r"\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def filled_cells(rows: list[list[str | None]]) -> int:
    return sum(1 for row in rows for cell in row if cell and cell.strip())


def is_meaningful_table(rows: list[list[str | None]]) -> bool:
    """尺寸/密度过滤：剔除版式装饰框和被拆散的图形文字，只留真实数据表。"""
    n_rows = len(rows)
    n_cols = max((len(row) for row in rows), default=0)
    filled = filled_cells(rows)
    if n_rows < 2 or n_cols < 2 or filled < config.TABLE_MIN_FILLED_CELLS:
        return False
    if n_cols > config.TABLE_MAX_COLS or n_rows * n_cols > config.TABLE_MAX_CELLS:
        return False
    if n_cols <= config.TABLE_FILL_RATIO_COLS_WAIVER:
        return True  # 包头示意图类（IP/TCP 报头）合并单元格多，密度天然偏低
    return filled / (n_rows * n_cols) >= config.TABLE_MIN_FILL_RATIO


def char_coverage(rows: list[list[str | None]], region_text: str) -> float:
    """抽取单元格对 bbox 区域原文字的字符覆盖率，防止 find_tables 静默丢字。"""
    def normalize(value: str | None) -> str:
        return re.sub(r"\s+", "", value or "")
    cells_text = normalize("".join(cell or "" for row in rows for cell in row))
    return len(cells_text) / max(len(normalize(region_text)), 1)


def to_markdown(rows: list[list[str | None]]) -> str:
    """序列化为 markdown 表格，首行作表头（超长切分时按表头重复）。"""
    cleaned = [[clean_cell(cell) for cell in row] for row in rows]
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    lines = [
        "| " + " | ".join(cell or " " for cell in cleaned[0]) + " |",
        "|" + "---|" * width,
    ]
    for row in cleaned[1:]:
        lines.append("| " + " | ".join(cell or " " for cell in row) + " |")
    return "\n".join(lines)


def extract_page_tables(page: "pymupdf.Page") -> list[dict]:
    """检测并过滤单页表格，返回 [{bbox, markdown}]，顺序自上而下。"""
    tables = []
    try:
        finder = page.find_tables()
    except Exception:
        return []
    for table in finder.tables:
        rows = table.extract()
        if not is_meaningful_table(rows):
            continue
        bbox = pymupdf.Rect(table.bbox)
        region_text = page.get_text("text", clip=bbox)
        if char_coverage(rows, region_text) < config.TABLE_MIN_CHAR_COVERAGE:
            continue
        tables.append({
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            "markdown": to_markdown(rows),
        })
    tables.sort(key=lambda item: item["bbox"][1])
    return tables


def find_caption(text_blocks: list[tuple], table_bbox: "pymupdf.Rect") -> str:
    """取表格正上方最近的正文块作表题/引导语；过远或过长的放弃。"""
    candidates = [
        block for block in text_blocks
        if block[3] <= table_bbox.y0 + 4 and block[3] >= table_bbox.y0 - 90
    ]
    if not candidates:
        return ""
    closest = max(candidates, key=lambda block: block[3])
    caption = closest[4].strip().splitlines()[0].strip() if closest[4] else ""
    if len(caption) > config.TABLE_CONTEXT_MAX_CHARS:
        caption = caption[:config.TABLE_CONTEXT_MAX_CHARS].rstrip("，、 ")
    return caption


def body_text_without_tables(text_blocks: list[tuple], bboxes: list["pymupdf.Rect"]) -> str:
    """按阅读顺序重组正文，丢弃落在任一表格 bbox 内的文本块。"""
    kept = []
    for block in text_blocks:
        if block[6] != 0:  # 非文本块（图片）
            continue
        rect = pymupdf.Rect(block[:4])
        area = max(rect.get_area(), 1.0)
        overlap = 0.0
        for bbox in bboxes:
            inter = rect & bbox
            if not inter.is_empty:
                overlap += inter.get_area()
        if overlap / area > 0.5:
            continue
        lines = [line.strip() for line in block[4].splitlines() if line.strip()]
        if lines:
            kept.append((round(block[1]), round(block[0]), "\n".join(lines)))
    kept.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(item[2] for item in kept)


def split_table_markdown(markdown: str, header_line: str, max_chars: int) -> list[str]:
    """超长表格按行切分，每段重复表头行，保证行仍能对上列名。"""
    lines = markdown.splitlines()
    # lines[0]=表头，lines[1]=markdown 分隔行，数据行从 lines[2] 开始
    if len(header_line) > max_chars:
        header_line = header_line[:max_chars]
    parts: list[list[str]] = []
    current: list[str] = []
    current_len = len(header_line)
    for line in lines[2:]:
        if current and current_len + len(line) + 1 > max_chars:
            parts.append(current)
            current, current_len = [], len(header_line)
        current.append(line)
        current_len += len(line) + 1
    if current:
        parts.append(current)
    return [f"{header_line}\n" + "\n".join(part) for part in parts]
