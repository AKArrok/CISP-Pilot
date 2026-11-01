import json
from types import SimpleNamespace

import pytest

from scripts.eval_source_locator_loop import BoundedCalls


def client(usage, finish="stop"):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(model="deepseek-flash", usage=usage, choices=[SimpleNamespace(
            finish_reason=finish, message=SimpleNamespace(content="raw response", reasoning_content=None))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), requests


def test_unknown_usage_preserves_raw_and_prevents_second_dispatch(tmp_path):
    stub, requests = client(None)
    calls = BoundedCalls(stub, tmp_path)
    with pytest.raises(ValueError, match="UnknownUsage"):
        calls.invoke([{"role": "user", "content": "test"}], "one", "answer")
    with pytest.raises(RuntimeError):
        calls.invoke([{"role": "user", "content": "test"}], "two", "answer")
    row = json.loads((tmp_path / "provider-receipts.jsonl").read_text(encoding="utf-8"))
    assert row["raw"] == "raw response" and calls.unknown and len(requests) == 1


@pytest.mark.parametrize("max_calls", [2, 16])
def test_call_limit_and_flash_non_thinking_request(tmp_path, max_calls):
    stub, requests = client({"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})
    calls = BoundedCalls(stub, tmp_path, max_calls=max_calls)
    for i in range(max_calls):
        calls.invoke([{"role": "user", "content": "test"}], str(i), "answer")
    with pytest.raises(RuntimeError, match="BudgetOrDeadline"):
        calls.invoke([{"role": "user", "content": "test"}], "seventeen", "answer")
    assert len(requests) == max_calls and calls.spent < .10
    assert all(r["model"] == "deepseek-flash" and r["extra_body"]["thinking"]["type"] == "disabled"
               and r["max_tokens"] == 1024 for r in requests)
