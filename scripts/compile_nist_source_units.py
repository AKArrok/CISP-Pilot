"""Compile literal source units, not inferred facts or independent propositions."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.eval_nist_batch import request_content
from scripts.inline_oscal_parameters import parameter_bindings


def compile_units(chunks):
    sources = {}
    for chunk in chunks:
        key = (chunk["source_url"], chunk["locator"], chunk["text"])
        sources.setdefault(key, {k: chunk[k] for k in ("source", "source_url", "locator", "text")})
    output = []
    for source in sources.values():
        digest = hashlib.sha256(source["text"].encode()).hexdigest()
        payload = json.loads(request_content("answer", {"question": ""}, {"contexts": [source]}, "",
            {"evidence_protocol": "answer_source_lines", "guidance_segments": True}))
        lines = payload["canonical_contexts"][0]["lines"]
        units = []
        for line in lines:
            original = source["text"].splitlines()[line["line"] - 1]
            segments = line.get("segments", [line.get("text")])
            assert "".join(segments) == original
            position = 0
            for part, text in enumerate(segments, 1):
                end = position + len(text)
                assert original[position:end] == text
                units.append({"id": f"{digest}:{line['line']}:{part}", "parent_line": line["line"],
                    "part": part, "start_codepoint": position, "end_codepoint": end, "text": text})
                position = end
        output.append({"source": source["source"], "source_url": source["source_url"], "locator": source["locator"],
            "canonical_text_sha256": digest, "units": units, "parameter_bindings": parameter_bindings(source["text"])})
    return {"scope": "Literal ordered original source spans only; neighboring units can share referents/conditions. Not inferred independent facts or semantic entailment.",
        "input_documents": len(chunks), "canonical_sources": len(output),
        "contains_questions_labels_or_answers": False, "sources": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunks", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve prior source-unit compilation")
    compiled = compile_units(json.loads(args.chunks.read_text(encoding="utf-8")))
    compiled["input_sha256"] = hashlib.sha256(args.chunks.read_bytes()).hexdigest()
    compiled["compiler_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    compiled["runtime_dependencies"] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in ("scripts/eval_nist_batch.py", "scripts/inline_oscal_parameters.py", "scripts/answer_grade_contract.py")}
    args.output.write_text(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": compiled["input_documents"], "canonical_sources": compiled["canonical_sources"],
        "units": sum(len(source["units"]) for source in compiled["sources"])}))
