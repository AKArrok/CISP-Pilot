"""Align complete source translations by native clause IDs and ordered sentences."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Source-only translations of all native definitions in the four full controls.
# These are AI reading aids, not NIST official translations or answer labels.
parameter_zh = {
    "ac-08_odp.01": "系统在授予用户访问权限前显示的系统使用通知消息或横幅，待定义。",
    "ac-08_odp.02": "系统在授予进一步访问权限前显示的系统使用条件，待定义。",
    "at-2_prm_1": "由组织定义的频次，未赋值。",
    "at-2_prm_2": "由组织定义的事件，未赋值。",
    "at-02_odp.01": "初始培训之后向系统用户（包括经理、高管和承包商）提供安全素养培训的频次，待定义。",
    "at-02_odp.02": "初始培训之后向系统用户（包括经理、高管和承包商）提供隐私素养培训的频次，待定义。",
    "at-02_odp.03": "要求对系统用户开展安全素养培训的事件，待定义。",
    "at-02_odp.04": "要求对系统用户开展隐私素养培训的事件，待定义。",
    "at-02_odp.05": "用于提升系统用户安全与隐私意识的技术，待定义。",
    "at-02_odp.06": "更新素养培训与意识内容的频次，待定义。",
    "at-02_odp.07": "要求更新素养培训与意识内容的事件，待定义。",
    "cp-09_odp.01": "需要对其中的用户级信息执行备份的系统组件，待定义。",
    "cp-09_odp.02": "与恢复时间目标和恢复点目标一致的用户级信息备份频次，待定义。",
    "cp-09_odp.03": "与恢复时间目标和恢复点目标一致的系统级信息备份频次，待定义。",
    "cp-09_odp.04": "与恢复时间目标和恢复点目标一致的系统文档备份频次，待定义。",
    "ca-08_odp.01": "对系统或系统组件开展渗透测试的频次，待定义。",
    "ca-08_odp.02": "需要开展渗透测试的系统或系统组件，待定义。",
}
unit_file = ROOT / "docs/engineering-delivery/evaluation-resume-20261106/literal-source-units.json"
aid_file = ROOT / "evaluation/nist-reading-guidance-v4-20261107/reading-aids.json"
chunk_file = ROOT / "evaluation/nist-unit-evidence-first-v1-20261107/chunks.json"
sources, aids, chunks = read(unit_file)["sources"], read(aid_file), read(chunk_file)
originals = {hashlib.sha256(chunk["text"].encode()).hexdigest(): chunk for chunk in chunks}
translations, parameters, alignments = {}, {}, []
for source in sources:
    control = source["locator"]
    if control not in aids:
        continue
    digest = source["canonical_text_sha256"]
    assert aids[control]["canonical_text_sha256"] == digest
    original = originals[digest]
    head, found, tail = aids[control]["reading_translation"].partition(f"[{control}_gdn]")
    assert found
    matches = list(re.finditer(r"(?m)^\[([^]]+)\]\s*", head))
    clauses = {}
    for index, match in enumerate(matches):
        end = matches[index+1].start() if index+1 < len(matches) else len(head)
        clauses[match[1]] = "".join(line.strip() for line in head[match.end():end].splitlines()
                                   if line.strip() != "以及")
    title = head[:matches[0].start()].strip()
    if control == "at-2":
        title = "素养培训与意识教育"
    guidance = [line.strip() for line in tail.splitlines() if line.strip()]
    guidance_units, started = [], False
    for unit in source["units"]:
        text = unit["text"]
        if "_gdn]" in text:
            started = True
        if text.startswith("Original OSCAL parameter"):
            break
        if started and text.strip():
            guidance_units.append(unit)
    assert len(guidance_units) == len(guidance)
    guidance_map = {unit["id"]: text for unit, text in zip(guidance_units, guidance, strict=True)}
    for unit in source["units"]:
        text = unit["text"]
        if not text.strip():
            continue
        tag = re.match(r"\[([^]]+)\]", text)
        if unit["id"] in guidance_map:
            chinese = guidance_map[unit["id"]]
            if control == "at-2" and "_gdn]" in text:
                chinese = "组织向系统用户提供基础级和高级别的素养培训，包括测试用户知识水平的措施。"
        elif tag and tag[1] in clauses:
            chinese = clauses[tag[1]]
        elif unit["parent_line"] == 1:
            chinese = title
        elif text.startswith("Original OSCAL parameter"):
            chinese = "原生OSCAL参数定义（本次提取未赋值）。"
        elif text.startswith("[{"):
            definitions = json.loads(text)
            chinese = "\n".join(parameter["id"] + "：" + parameter_zh[parameter["id"]] for parameter in definitions)
            for parameter in definitions:
                parameters[parameter["id"]] = {"original_definition": parameter,
                    "reading_translation": parameter_zh[parameter["id"]], "source_text_sha256": digest}
        else:
            raise ValueError("Unaligned source unit: " + unit["id"])
        translations[unit["id"]] = {"english_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "reading_translation": chinese, "authority": "Unofficial AI source translation, not primary evidence or human gold"}
    alignments.append({"control": control, "canonical_text_sha256": digest,
        "guidance_units": len(guidance_units), "guidance_translations": len(guidance),
        "review": "Codex AI pairwise original-English review; source-only, no question/answer/label input"})
assert len(parameters) == len(parameter_zh)
target = HERE / "bilingual-units.json"
if target.exists():
    raise FileExistsError("Preserve prior source translations")
target.write_text(json.dumps({"scope": "All nonblank units of four complete controls; no questions, answers or expected labels; not indexed",
    "units": translations, "parameters": parameters, "alignment": alignments,
    "input_sha256": {path.relative_to(ROOT).as_posix(): sha(path) for path in (unit_file, aid_file, chunk_file)},
    "compiler_sha256": sha(Path(__file__)),
    "translation_corrections": ["AT2 title and firstguidance literacy training rendered as 素养培训, awareness remains distinct; original English unchanged"],
    "limit": "Pair count and offsets prove alignment, not semantic translation fidelity; review is AI, not independent human"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"controls": len(alignments), "bilingual_units": len(translations), "native_parameters": len(parameters)}))
