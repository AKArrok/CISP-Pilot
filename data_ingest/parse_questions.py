"""解析 CISP 真题 → data/questions.json（结构化题库）+ data/parse_failures.json

三个来源:
1. cisp试题700道(带答案).pdf — 表格布局，两个题库段（题号各自从 1 开始），
   通过 PyMuPDF find_tables 按行提取；跨页续行合并到上一题
2. CISP216.docx — 与 700题PDF 第二段同源，带"知识点"解析但无答案 → 按题号
   回填解析（题干前缀校验防止错位）
3. 51CTO 六套题 docx — 答案字母跟在题干段末尾

知识域打标: 关键词规则（domains.classify_domain_by_keywords）；
--llm-classify 开启后对规则未命中的题目用轻量 LLM 批量兜底分类。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from domains import DOMAINS, classify_domain_by_keywords, normalize_for_match

NUM_RE = re.compile(
    r"^\s*(\d{1,3})(?:\s*[.．、\\:：]\s*|\s*[-—]{1,}\s*|\s+(?=\S)|"
    r"(?=[\u4e00-\u9fffA-Za-z“《]))"
)
OPT_RE = re.compile(
    r"^\s*([A-F])(?:\s*[.．、:：]\s*|\s+(?=\S)|(?=[\u4e00-\u9fff“《]))(.*)$",
    re.S,
)
OPT_CELL_RE = re.compile(r"^\s*[A-F]\s*[.．、]\s*")   # 700题PDF 选项单元格自带字母前缀
ANSWER_RE = re.compile(r"\s*([A-D]{1,4})\s*$")
KNOWN_RE = re.compile(r"^\s*(知识点|解析|考点|答案)\s*[：:]")


def new_question(source: str, num: int) -> dict:
    return {
        "id": f"{source}_{num}",
        "source": source,
        "num": num,
        "stem": "",
        "options": {},
        "answer": "",
        "analysis": "",
        "domain": None,
        "needs_review": False,
    }


def clean(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", text or "").strip()


def _looks_like_question(text: str) -> bool:
    return bool(re.search(r"[?？]|(?:以下|下列|哪一|哪种|是否|说法).{0,30}[：:]?", text))


def _embedded_next_number(question: dict) -> bool:
    """检测被拼进题干的下一题题号，避免“选项齐全”掩盖解析污染。"""
    next_num = int(question.get("num", 0)) + 1
    body = re.sub(r"^\s*\d{1,3}\s*[.．、\\:：]?\s*", "", question.get("stem", ""), count=1)
    return bool(re.search(
        rf"(?<!\d){next_num}(?!\d)(?:\s*[.．、,，:：]\s*|"
        rf"\s+(?=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))",
        body,
    ))


# ────────────────────────────────────────────────────────────────────────
# 1. 700 题 PDF（表格）
# ────────────────────────────────────────────────────────────────────────

def parse_pdf700() -> list[dict]:
    import pymupdf

    doc = pymupdf.open(os.path.join(config.EXAM_DIR, "cisp试题700道(带答案).pdf"))
    rows = []
    for page in doc:
        for table in page.find_tables().tables:
            rows.extend(table.extract())

    questions: list[dict] = []
    bank = 1
    failures: list[dict] = []
    for r in rows:
        cells = [clean(c).replace("\n", "") for c in r]  # 表格单元格内换行是折行，直接拼回
        stem, opts, answer = cells[0], cells[1:5], cells[5] if len(cells) > 5 else ""
        if stem == "题目内容":          # 表头
            continue
        m = NUM_RE.match(stem)
        if m:
            num = int(m.group(1))
            q = new_question(f"700题_b{bank}", num)
            q["stem"] = stem
            for letter, text in zip("ABCD", opts, strict=False):
                if text:
                    q["options"][letter] = OPT_CELL_RE.sub("", text)
            q["answer"] = answer if re.fullmatch(r"[A-D]{1,4}", answer) else ""
            if not q["answer"]:
                q["needs_review"] = True
                failures.append({"source": q["id"], "reason": "answer_missing_or_invalid", "raw": cells})
            questions.append(q)
        elif questions:                 # 跨页续行：按列拼回上一题
            prev = questions[-1]
            prev["stem"] += stem
            for letter, text in zip("ABCD", opts, strict=False):
                if text and letter in prev["options"]:
                    prev["options"][letter] += text
                elif text:
                    prev["options"][letter] = text
        else:
            failures.append({"source": "700题", "reason": "orphan_row", "raw": cells})

    # 题号 545 之后是第二段（题号重启）：上面 bank 逻辑没用上，这里按序号重置点重打 id
    return questions, failures


def split_banks(questions: list[dict]) -> list[dict]:
    """按题号重起点把 700 题拆成两段并重写 id/source。"""
    out = []
    bank = 1
    prev_num = 0
    for index, q in enumerate(questions):
        if q["num"] < prev_num:
            if q["num"] <= 5 and prev_num >= 100:
                bank += 1
            else:
                q["needs_review"] = True
                q["sequence_conflict"] = {"previous_num": prev_num, "parsed_num": q["num"]}
                q["source"] = f"700题_b{bank}"
                q["id"] = f"700题_b{bank}_unsequenced_{index}_{q['num']}"
                out.append(q)
                continue
        prev_num = q["num"]
        q["source"] = f"700题_b{bank}"
        q["id"] = f"700题_b{bank}_{q['num']}"
        out.append(q)
    return out


# ────────────────────────────────────────────────────────────────────────
# 2. CISP216.docx（知识点解析回填）
# ────────────────────────────────────────────────────────────────────────

def parse_cisp216() -> tuple[list[dict], list[str]]:
    import docx

    path = os.path.join(config.EXAM_DIR, "CISP216.docx")
    doc = docx.Document(path)
    questions, failures = [], []
    cur = None
    for p in doc.paragraphs:
        text = clean(p.text)
        if not text:
            continue
        if KNOWN_RE.match(text):
            if cur is not None:
                cur["analysis"] += ("\n" if cur["analysis"] else "") + text
            continue
        om = OPT_RE.match(text)
        if om and cur is not None:
            cur["options"][om.group(1)] = clean(om.group(2))
            continue
        nm = NUM_RE.match(text)
        if nm:
            cur = new_question("CISP216", int(nm.group(1)))
            cur["stem"] = text
            questions.append(cur)
            continue
        # 其它行：既不是题号/选项/知识点 → 视为上一条解析的续行
        if cur is not None and cur["analysis"]:
            cur["analysis"] += text
        elif cur is not None:
            cur["stem"] += text
    return questions, failures


def merge_analysis(bank2: list[dict], cisp216: list[dict]) -> None:
    """CISP216 的"知识点"按题号回填到 700题_b2，题干前缀校验防错位。"""
    by_num = {q["num"]: q for q in bank2}
    for q in cisp216:
        target = by_num.get(q["num"])
        if target is None:
            continue
        a, b = normalize_for_match(target["stem"])[:15], normalize_for_match(q["stem"])[:15]
        if a[:10] == b[:10]:
            if not target["analysis"]:
                target["analysis"] = q["analysis"]
        else:
            target.setdefault("_merge_conflicts", []).append(q["num"])


# ────────────────────────────────────────────────────────────────────────
# 3. 51CTO 六套 docx（答案跟在题干末尾）
# ────────────────────────────────────────────────────────────────────────

KEY_LINE_RE = re.compile(r"^\s*(\d{1,3})\s*[\.、,，:：]?\s*([A-Da-d])\s*$")
KEY_COMPACT_RE = re.compile(r"^\s*(?:\d{1,3}\s*[A-Da-d]\s*){2,}$")
TIME_RE = re.compile(r"^\s*\d{1,2}\s*[:：]\s*\d{2}\b")
INLINE_OPTION_RE = re.compile(r"(?:^|\s)([A-D])\s*[.．、:：]\s*")


def _inline_options(text: str) -> dict[str, str]:
    matches = list(INLINE_OPTION_RE.finditer(text))
    if len(matches) < 2:
        return {}
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1)] = clean(text[match.end():end])
    return result


def _parse_51cto_paragraphs(source: str, paragraphs: list[str]) -> tuple[list[dict], list[dict]]:
    """解析单套 51CTO 段落；宽容输入格式，但把推断编号和异常结构隔离。"""
    questions: list[dict] = []
    failures: list[dict] = []
    key_map: dict[int, str] = {}
    cur = None
    last_option: str | None = None

    for raw in paragraphs:
        text = clean(raw)
        if not text:
            continue
        key_match = KEY_LINE_RE.match(text)
        if key_match:
            key_map[int(key_match.group(1))] = key_match.group(2).upper()
            continue
        if KEY_COMPACT_RE.match(text):
            for num, answer in re.findall(r"(\d{1,3})\s*([A-Da-d])", text):
                key_map[int(num)] = answer.upper()
            continue
        if cur is not None and re.fullmatch(r"\d{1,3}", text):
            failures.append({
                "source": f"{source}_{text}", "reason": "answer_key_missing",
            })
            continue

        option_match = OPT_RE.match(text)
        if option_match and cur is not None:
            inline = _inline_options(text)
            if inline:
                cur["options"].update(inline)
                last_option = next(reversed(inline))
                continue
            last_option = option_match.group(1)
            cur["options"][last_option] = clean(option_match.group(2))
            continue

        number_match = None if TIME_RE.match(text) else NUM_RE.match(text)
        if number_match:
            parsed_num = int(number_match.group(1))
            if cur is not None and parsed_num <= cur["num"] and len(cur["options"]) < 2:
                # 题干内的“1、…2、…”枚举项，不是题库编号。
                cur["stem"] += " " + text
                continue
            num = parsed_num
            inferred = False
            if cur is not None and parsed_num <= cur["num"]:
                # 原始题库偶有错写题号。按位置补连续编号，但必须进入人工复核。
                num = cur["num"] + 1
                inferred = True
            cur = new_question(source, num)
            cur["stem"] = text
            if inferred:
                cur["needs_review"] = True
                cur["original_num"] = parsed_num
                failures.append({
                    "source": cur["id"], "reason": "out_of_sequence_number",
                    "original_num": parsed_num, "inferred_num": num,
                })
            questions.append(cur)
            last_option = None
            continue

        if cur is not None and len(cur["options"]) >= 2 and _looks_like_question(text):
            # 上一题已经完整，当前段又明显是新题但没有题号；保留并隔离，禁止继续粘连。
            cur = new_question(source, cur["num"] + 1)
            cur["stem"] = text
            cur["needs_review"] = True
            cur["number_inferred"] = True
            questions.append(cur)
            failures.append({
                "source": cur["id"], "reason": "question_number_inferred",
                "stem": text[:80],
            })
            last_option = None
            continue

        if cur is None:
            failures.append({"source": source, "reason": "orphan_paragraph", "raw": text[:120]})
        elif last_option is not None:
            cur["options"][last_option] += text
        else:
            cur["stem"] += text

    extract_inline_answers(questions)
    for question in questions:
        if not question["answer"] and question["num"] in key_map:
            question["answer"] = key_map[question["num"]]
            if not question.get("number_inferred") and "original_num" not in question:
                question["needs_review"] = False
    failures.extend(failures_of_incomplete(questions, source))
    return questions, failures


def parse_51cto_sets() -> tuple[list[dict], list[dict]]:
    """51CTO 六套题，答案有三种形态:
    - 一/二套: 答案字母紧跟题干末尾（'...：C'）
    - 二/三/四/五套: 文末答案对照表（'72\\tD' 整段）
    - 六套: 压缩答案串穿插正文（'1C2A3D4B5C' 每5题一批）
    """
    questions, failures = [], []
    files = sorted(
        f for f in os.listdir(config.EXAM_DIR)
        if f.startswith("51CTO下载-CISP试题及答案") and f.endswith(".docx")
    )
    import docx

    for fname in files:
        set_name = re.search(r"(一|二|三|四|五|六)套题", fname)
        source = f"51CTO_{set_name.group(1)}套" if set_name else fname
        doc = docx.Document(os.path.join(config.EXAM_DIR, fname))
        parsed, source_failures = _parse_51cto_paragraphs(
            source, [paragraph.text for paragraph in doc.paragraphs])
        questions.extend(parsed)
        failures.extend(source_failures)
    return questions, failures


def failures_of_incomplete(questions: list[dict], source: str) -> list[dict]:
    out = []
    for q in questions:
        if q["source"] != source:
            continue
        if len(q["options"]) < 2 or not q["stem"]:
            q["needs_review"] = True
            out.append({"source": q["id"], "reason": "incomplete_options", "stem": q["stem"][:60]})
    return out


def extract_inline_answers(questions: list[dict]) -> None:
    """51CTO 格式：答案字母紧跟在题干末尾（'...：C'），拆出来。"""
    for q in questions:
        if q["answer"]:
            continue
        m = ANSWER_RE.search(q["stem"])
        if m and len(q["options"]) >= 2:
            q["answer"] = m.group(1)
            q["stem"] = q["stem"][: m.start()].rstrip("：: ，,")
        else:
            q["needs_review"] = True


# ────────────────────────────────────────────────────────────────────────
# 4. 去重 / 打标 / 自检
# ────────────────────────────────────────────────────────────────────────

def quarantine_structural_failures(questions: list[dict]) -> list[dict]:
    """隔离仍含下一题题号或异常超长题干的记录。"""
    failures = []
    for question in questions:
        sequence_conflict = question.get("sequence_conflict")
        if not (sequence_conflict or _embedded_next_number(question)
                or len(question.get("stem", "")) > 1000):
            continue
        question["needs_review"] = True
        failures.append({
            "source": question["id"],
            "reason": "sequence_conflict" if sequence_conflict
            else "embedded_question_or_oversized_stem",
            "stem": question.get("stem", "")[:120],
        })
    return failures

def dedupe(questions: list[dict]) -> tuple[list[dict], int]:
    """跨来源按题干前缀去重：后出现的重复题标记 dup_of（保留先出现的，其来源题库优先级更高）。"""
    seen: dict[str, str] = {}
    dups = 0
    for q in questions:
        key = normalize_for_match(q["stem"])[:40]
        if key in seen:
            q["dup_of"] = seen[key]
            dups += 1
        else:
            seen[key] = q["id"]
    return questions, dups


def classify_domains(questions: list[dict], use_llm: bool) -> None:
    unclassified = []
    for q in questions:
        text = q["stem"] + " " + " ".join(q["options"].values()) + " " + q["analysis"]
        q["domain"] = classify_domain_by_keywords(text)
        if q["domain"] is None and not q.get("dup_of"):
            unclassified.append(q)
    if not use_llm or not unclassified:
        return
    from pydantic import BaseModel
    from llms import get_simple_llm, invoke_structured

    class Batch(BaseModel):
        labels: list[str]  # 与输入题目一一对应，取值必须是十大知识域之一或 "未知"

    llm = get_simple_llm()
    for i in range(0, len(unclassified), 20):
        batch = unclassified[i:i + 20]
        lines = "\n".join(f"{j}. {q['stem'][:120]}" for j, q in enumerate(batch))
        msg = [
            {"role": "system", "content":
                "你是 CISP 考试题分类器。十大知识域: " + "、".join(DOMAINS) +
                "。对每道题判断其所属知识域。只输出 JSON。"},
            {"role": "user", "content": f"题目列表:\n{lines}"},
        ]
        try:
            result = invoke_structured(llm, Batch, msg)
            for q, label in zip(batch, result.labels, strict=False):
                if label in DOMAINS:
                    q["domain"] = label
        except Exception as e:
            print(f"[警告] LLM 分类批次失败: {e}")
            break

    # 逐题重试仍缺域的（批量 zip 错位/输出"未知"的漏网之鱼）
    class Single(BaseModel):
        domain: str

    still = [q for q in unclassified if q["domain"] is None]
    for q in still:
        try:
            out = invoke_structured(llm, Single, [
                {"role": "system", "content":
                    "你是 CISP 考试题分类器。判断该题所属的十大知识域之一: "
                    + "、".join(DOMAINS) + "。只输出 JSON。"},
                {"role": "user", "content": q["stem"][:200]},
            ])
            if out.domain in DOMAINS:
                q["domain"] = out.domain
        except Exception:
            continue


def self_check(questions: list[dict]) -> dict:
    stats = {"total": len(questions)}
    by_source: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    no_answer = no_domain = needs_review = dups = 0
    for q in questions:
        by_source[q["source"]] = by_source.get(q["source"], 0) + 1
        by_domain[q["domain"] or "未分类"] = by_domain.get(q["domain"] or "未分类", 0) + 1
        if not q["answer"]:
            no_answer += 1
        if not q["domain"]:
            no_domain += 1
        # 答案字母超出实际选项 / 知识域缺失 → 不可用于出题
        if q["answer"] and not set(q["answer"]) <= set(q["options"]):
            q["needs_review"] = True
        if q["domain"] is None:
            q["needs_review"] = True
        if q.get("needs_review"):
            needs_review += 1
        if q.get("dup_of"):
            dups += 1
    stats.update(by_source=by_source, by_domain=by_domain, no_answer=no_answer,
                 no_domain=no_domain, needs_review=needs_review, duplicates=dups)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-classify", action="store_true",
                    help="对关键词规则未命中的题目用轻量 LLM 兜底分类")
    args = ap.parse_args()

    all_questions: list[dict] = []
    failures: list[dict] = []

    # 1) 700 题 PDF → 两段题库
    pdf_qs, pdf_failures = parse_pdf700()
    pdf_qs = split_banks(pdf_qs)
    print(f"700题PDF: {len(pdf_qs)} 题（b1={sum(1 for q in pdf_qs if q['source']=='700题_b1')}, "
          f"b2={sum(1 for q in pdf_qs if q['source']=='700题_b2')}）")
    all_questions.extend(pdf_qs)
    failures.extend(pdf_failures)

    # 2) CISP216 知识点回填到 b2
    cisp_qs, _ = parse_cisp216()
    bank2 = [q for q in pdf_qs if q["source"] == "700题_b2"]
    merge_analysis(bank2, cisp_qs)
    merged = sum(1 for q in bank2 if q["analysis"])
    print(f"CISP216.docx: {len(cisp_qs)} 题，回填解析 {merged} 题")

    # 3) 51CTO 六套
    set_qs, set_failures = parse_51cto_sets()
    print(f"51CTO 六套: {len(set_qs)} 题")
    all_questions.extend(set_qs)
    failures.extend(set_failures)

    # 4) 结构隔离 → 去重 → 打标 → 自检
    failures.extend(quarantine_structural_failures(all_questions))
    all_questions, dups = dedupe(all_questions)
    classify_domains(all_questions, use_llm=args.llm_classify)
    stats = self_check(all_questions)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.QUESTIONS_PATH, "w", encoding="utf-8") as fp:
        json.dump(all_questions, fp, ensure_ascii=False, indent=1)
    with open(config.PARSE_FAILURES_PATH, "w", encoding="utf-8") as fp:
        json.dump(failures, fp, ensure_ascii=False, indent=1)
    from data_ingest.manifest import update_manifest, QUESTIONS_SCHEMA_VERSION
    update_manifest(question_count=len(all_questions), questions_schema_version=QUESTIONS_SCHEMA_VERSION)

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"解析失败/待复核 {len(failures)} 条 → {config.PARSE_FAILURES_PATH}")
    print(f"题库 {len(all_questions)} 题 → {config.QUESTIONS_PATH}")


if __name__ == "__main__":
    main()
