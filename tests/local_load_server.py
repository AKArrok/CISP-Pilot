"""Synthetic load fixture with bounded replies; real HTTP, graph and persistence."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage  # noqa: E402
import uvicorn  # noqa: E402
from agents import graph, memory  # noqa: E402
from data_ingest import manifest  # noqa: E402


async def route(state):
    return {"intent": "chitchat", "domain": None}


async def reply(state):
    answer = "合成负载测试回答：已收到本轮请求。"
    return {"answer": answer, "messages": [AIMessage(content=answer)], "usage": {}}


if __name__ == "__main__":
    target = Path(os.environ["CISP_DB_PATH"]).resolve()
    if target == (Path(__file__).resolve().parents[1] / "data/cisp_qa.db").resolve():
        raise ValueError("Load fixture requires an explicit isolated database")
    graph.route = route
    graph.chitchat = reply
    memory._save_vector_round = lambda *_: None
    manifest.validate_index = lambda **_: None
    import server

    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
