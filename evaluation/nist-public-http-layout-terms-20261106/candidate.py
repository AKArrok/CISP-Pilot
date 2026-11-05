"""Apply one frozen evaluation prompt delta without editing the default prompt."""
import hashlib
import json
from pathlib import Path
import sys


def main():
    packet = Path(__file__).resolve().parent
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != manifest["candidate_wrapper_sha256"]:
        raise ValueError("Frozen candidate wrapper changed")
    if Path(sys.argv[sys.argv.index("--packet") + 1]).resolve() != packet:
        raise ValueError("Candidate packet mismatch")
    sys.path.insert(0, str(packet.parents[1]))
    from agents import prompts
    from scripts.evaluate_public_http import main as execute
    if hashlib.sha256(prompts.ANSWER_SYSTEM.encode()).hexdigest() != manifest["baseline_answer_sha256"]:
        raise ValueError("Default answer prompt differs from frozen baseline")
    prompts.ANSWER_SYSTEM = json.loads((packet / "prompts.json").read_text(encoding="utf-8"))["ANSWER_SYSTEM"]
    execute()


if __name__ == "__main__":
    main()
