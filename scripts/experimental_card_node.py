"""Opt-in evaluation node; normal answers and relevance gates remain available."""
import hashlib
import json

from langchain_core.messages import HumanMessage, SystemMessage
import tiktoken

from agents import graph
from agents.tracing import observe
from agents.usage import add_receipts, receipt
from llms import llm_ainvoke_with_retry
from scripts.answer_unit_contract import build_catalog, materialize_card_selection, render_card_answer
from scripts.eval_nist_batch import request_content


async def card_answer(state, *, sources, translations, selector_prompt, fallback):
    contexts = state.get("contexts", [])
    if not contexts or graph._below_relevance_floor(contexts):
        return await fallback(state)
    known = {(source["source_url"], source["locator"]): source["canonical_text_sha256"] for source in sources}
    for context in contexts:
        location = (context.get("source_url"), context.get("locator"))
        if location not in known:
            observe("answer_source_cards", "fallback_unmapped_source")
            return await fallback(state)
        if hashlib.sha256(context["text"].encode()).hexdigest() != known[location]:
            raise ValueError("Frozen source text changed")
    content = json.loads(request_content("evidence_select", {"question": state["question"], "unit_sources": sources},
        {"contexts": contexts}, "", {"mode": "answer_card_pipeline"}))
    history = state.get("messages", [])[:-1]
    if history:
        content["conversation_context_not_evidence"] = [{"role": message.type, "text": str(message.content)[:300]}
                                                       for message in history[-4:]]
    if state.get("search_query") and state["search_query"] != state["question"]:
        content["resolved_query_not_evidence"] = state["search_query"]
    messages = [SystemMessage(content=selector_prompt), HumanMessage(content=json.dumps(content, ensure_ascii=False))]
    encoding = tiktoken.get_encoding("cl100k_base")
    if sum(len(encoding.encode(message.content)) for message in messages) > 5500:
        raise ValueError("Frozen input token limit exceeded before selector dispatch")
    llm = graph.get_answer_llm(model="deepseek-flash", temperature=0, max_tokens=1024, max_retries=0,
        extra_body={"thinking": {"type": "disabled"}}, response_format={"type": "json_object"}, disable_streaming=True)
    response = await llm_ainvoke_with_retry(llm, messages, max_retries=1)
    selected = materialize_card_selection(str(response.content), build_catalog(contexts, sources), contexts)
    answer = render_card_answer(selected, contexts, translations, question=state["question"])
    observe("answer_source_cards", "rendered")
    usage = add_receipts(receipt(response), (state.get("evidence_report") or {}).get("usage") or add_receipts())
    return {"messages": [response.model_copy(update={"content": answer})], "answer": answer, "usage": usage}
