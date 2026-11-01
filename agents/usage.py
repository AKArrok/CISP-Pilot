"""Provider token receipts: missing or invalid counts remain unknown."""
TOKEN_KEYS = ("prompt_tokens", "completion_tokens")
USAGE_SCOPE = "answer_and_evidence_receipts_only_excludes_router_rewrite_embedding_reranking_unobserved_retries"


def token_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def receipt(message):
    metadata = getattr(message, "usage_metadata", None) or {}
    return {"prompt_tokens": token_count(metadata.get("input_tokens")),
            "completion_tokens": token_count(metadata.get("output_tokens"))}


def check_output_budget(message):
    from agents.budget import current_budget
    budget = current_budget.get()
    count = receipt(message)["completion_tokens"]
    if budget is not None and count is not None and count > budget.max_output_tokens:
        budget.cancel("output_limit")


def service_receipt(response):
    def field(value, key):
        return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
    usage = field(response, "usage")
    prompt = field(usage, "prompt_tokens")
    completion = field(usage, "completion_tokens")
    return {"prompt_tokens": token_count(prompt if prompt is not None else field(usage, "input_tokens")),
            "completion_tokens": token_count(completion if completion is not None else field(usage, "output_tokens")),
            "total_tokens": token_count(field(usage, "total_tokens"))}


def add_receipts(*items):
    result = {}
    for key in TOKEN_KEYS:
        counts = [token_count(item.get(key)) for item in items]
        result[key] = sum(counts) if all(count is not None for count in counts) else None
    return result
