"""One-shot public-source translations; never sends questions, labels or course data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(packet, output):
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen translation input changed")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != manifest["executor_sha256"]:
        raise ValueError("Frozen executor changed")
    segments = json.loads((packet / "segments.json").read_text(encoding="utf-8"))
    if not segments or len(segments) != manifest["max_calls"] or len({s["id"] for s in segments}) != len(segments):
        raise ValueError("Translation batch must be nonempty with unique IDs and frozen count")
    authorization = json.loads((packet / "authorization.json").read_text(encoding="utf-8"))
    if authorization != {"max_calls": len(segments), "budget_usd": manifest["budget_usd"],
                         "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()}:
        raise ValueError("Authorization mismatch")
    from dotenv import dotenv_values
    from openai import OpenAI
    import tiktoken
    import truststore
    truststore.inject_into_ssl()
    env = dotenv_values(ROOT / ".env", interpolate=False)
    client = OpenAI(api_key=os.getenv("LLM_API_KEY") or env.get("LLM_API_KEY"),
                    base_url="https://api.deepseek.com/v1", max_retries=0)
    encoding = tiktoken.get_encoding("cl100k_base")
    system = (packet / "system-prompt.txt").read_text(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=False)
    (output / "executor.py").write_bytes(Path(__file__).read_bytes())
    with (packet / "dispatch.lock").open("x", encoding="utf-8") as stream:
        stream.write("One bounded batch. Never delete to retry consumed calls.\n")
    summary = {"dispatch_attempts": 0, "known_cost_estimate_usd": 0.0, "cost_unknown": False,
               "completed_segments": 0, "status": "in_progress"}
    translations = []
    default = ROOT / "data/cisp_qa.db"
    before = hashlib.sha256(default.read_bytes()).hexdigest()
    try:
        for segment in segments:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": segment["source_text"]}]
            if (sum(len(encoding.encode(m["content"])) for m in messages) > 2000
                    or summary["known_cost_estimate_usd"] + .008 > manifest["budget_usd"]):
                raise ValueError("Frozen input or budget limit")
            summary["dispatch_attempts"] += 1
            summary["cost_unknown"] = True
            write(output / "summary.json", summary)
            started = time.monotonic()
            response = client.chat.completions.create(
                model="deepseek-flash", messages=messages, max_tokens=1024, temperature=0,
                extra_body={"thinking": {"type": "disabled"}}, timeout=60)
            with (output / "receipts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"id": segment["id"], "response": response.model_dump(),
                                         "elapsed_seconds": time.monotonic() - started}, ensure_ascii=False) + "\n")
            usage = response.usage
            if usage is None or usage.prompt_tokens is None or usage.completion_tokens is None:
                raise ValueError("Unknown provider usage")
            summary["cost_unknown"] = False
            summary["known_cost_estimate_usd"] += (usage.prompt_tokens * .3 + usage.completion_tokens * 1.2) / 1e6
            choice = response.choices[0]
            if (choice.finish_reason != "stop" or not choice.message.content
                    or usage.prompt_tokens > 2000 or usage.completion_tokens > 1024
                    or summary["known_cost_estimate_usd"] > manifest["budget_usd"]
                    or getattr(choice.message, "reasoning_content", None)):
                raise ValueError("Provider output outside frozen bounds")
            translations.append({"id": segment["id"], "control_id": segment["control_id"],
                                 "translation": choice.message.content})
            summary["completed_segments"] = len(translations)
            write(output / "translations.json", translations)
            write(output / "summary.json", summary)
            print(segment["id"] + ": translated", flush=True)
        summary["status"] = "completed"
    except Exception as error:
        summary.update(status="stopped", error_type=type(error).__name__,
                       http_status=getattr(error, "status_code", None))
        raise
    finally:
        write(output / "summary.json", summary)
        after = hashlib.sha256(default.read_bytes()).hexdigest()
        write(output / "default-db.json", {"before": before, "after": after, "unchanged": before == after})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.packet, args.output)
