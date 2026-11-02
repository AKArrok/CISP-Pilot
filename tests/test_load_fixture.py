"""The load substitute must not multiply prior assistant content each turn."""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

import local_load_server as fixture


def test_load_reply_stays_bounded_across_long_histories():
    short = {"messages": [HumanMessage(content="synthetic request")]}
    long = {"messages": [AIMessage(content="prior synthetic content" * 1000)] * 50}
    first = asyncio.run(fixture.reply(short))
    repeated = asyncio.run(fixture.reply(long))
    assert first["answer"] == repeated["answer"]
    assert len(repeated["answer"]) < 100
    assert repeated["messages"][0].content == repeated["answer"]
