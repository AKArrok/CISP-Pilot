"""Replay malformed native tool arguments through the real SDK, without API calls."""
import asyncio
import json

import httpx
import pytest
import llms

from agents import graph
from agents.budget import RequestBudget, current_budget
from agents.tracing import RequestTrace, current_trace


@pytest.mark.parametrize("malformed_json", [False, True])
def test_deepseek_route_uses_one_json_attempt(monkeypatch, malformed_json):
    payloads = []

    def handle(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if "tools" in payload:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "recorded-format-failure", "type": "function", "function": {
                    "name": "RouteOut", "arguments": '{"intent": "knowledge", "domain": 软件安全开发}'}}]}
        else:
            message = {"role": "assistant", "content": (
                '{"intent": "knowledge", "domain": 软件安全开发}' if malformed_json
                else json.dumps({"intent": "knowledge", "domain": "软件安全开发"}, ensure_ascii=False))}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}})

    trace = RequestTrace("route-json-fixture")
    trace_token = current_trace.set(trace)
    budget_token = current_budget.set(RequestBudget(max_attempts_per_call=1, max_output_tokens=256))
    try:
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(llms, "_simple_llm", None)
            model = llms.get_simple_llm(model="deepseek-flash", api_key="synthetic", base_url="https://fixture.invalid/v1",
                                        http_client=client, extra_body={"thinking": {"type": "disabled"}})
            monkeypatch.setattr(graph, "get_simple_llm", lambda: model)
            result = asyncio.run(graph.route({"question": "多语言使用告知的依据是什么？"}))
        assert len(payloads) == 1
        assert payloads[0].get("response_format") == {"type": "json_object"}
        assert "tools" not in payloads[0]
        assert payloads[0]["thinking"] == {"type": "disabled"}
        assert payloads[0]["max_tokens"] == 256
        degraded = any(event["outcome"] == "default_knowledge_degraded" for event in trace.events)
        assert degraded is malformed_json
        if not malformed_json:
            assert result == {"intent": "knowledge", "domain": "软件安全开发"}
    finally:
        current_budget.reset(budget_token)
        current_trace.reset(trace_token)
