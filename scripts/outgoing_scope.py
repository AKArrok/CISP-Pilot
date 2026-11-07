"""Hash-bound outgoing scope for explicitly restricted evaluation batches."""
import hashlib
import json
import re


def context_digest(context):
    canonical = {key: context.get(key) for key in ("source", "page", "source_url", "locator", "text")}
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_outgoing_scope(scope, question, contexts):
    if not isinstance(scope, dict) or set(scope) != {"questions"} or not isinstance(scope["questions"], dict):
        raise ValueError("Invalid outgoing scope")
    for digest, approved in scope["questions"].items():
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(approved, list) or not approved
                or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in approved)):
            raise ValueError("Invalid outgoing scope fingerprints")
    digest = hashlib.sha256(question.encode()).hexdigest()
    if scope["questions"].get(digest) != [context_digest(context) for context in contexts]:
        raise ValueError("Actual question or source contexts exceed outgoing scope")


def load_outgoing_scope(packet, manifest):
    enabled = manifest.get("enforce_outgoing_scope", False)
    if type(enabled) is not bool:
        raise ValueError("Outgoing scope flag must be boolean")
    if not enabled:
        return None
    if (manifest.get("answer_strategy") != "source_cards"
            or "outgoing-scope.json" not in manifest.get("frozen_sha256", {})):
        raise ValueError("Outgoing scope requires source_cards and frozen fingerprints")
    raw = (packet / "outgoing-scope.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["frozen_sha256"]["outgoing-scope.json"]:
        raise ValueError("Frozen outgoing scope fingerprints changed")
    scope = json.loads(raw)
    cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    questions = {hashlib.sha256(case["question"].encode()).hexdigest() for case in cases}
    if not isinstance(scope, dict) or set(scope) != {"questions"} or not isinstance(scope["questions"], dict):
        raise ValueError("Invalid outgoing scope")
    if not cases or len(questions) != len(cases) or set(scope["questions"]) != questions:
        raise ValueError("Outgoing scope must cover the exact frozen cases")
    # Validate all fingerprint shapes without loading any original private source text.
    for digest, contexts in scope["questions"].items():
        if (not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(contexts, list) or not contexts
                or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in contexts)):
            raise ValueError("Invalid outgoing scope fingerprints")
    return scope
