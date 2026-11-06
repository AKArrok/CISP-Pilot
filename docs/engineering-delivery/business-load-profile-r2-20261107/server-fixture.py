"""Prepare synthetic assets and sessions before the measured business workload."""
import json
import os
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


if __name__ == "__main__":
    output = Path(__file__).resolve().parent
    private = output / "operator-private"
    data = private / "demo"
    assert Path(os.environ["CISP_DB_PATH"]).resolve() == (data / "cisp_qa.db").resolve()
    from scripts.prepare_demo import prepare
    prepare(data)
    import config
    for name, file in {"KB_CHUNKS_PATH": "kb_chunks.json", "QUESTIONS_PATH": "questions.json",
                      "VECTORS_PATH": "vectors.npz", "MEMORY_VECTORS_PATH": "memory-unused.npz",
                      "KNOWLEDGE_TAXONOMY_PATH": "knowledge_taxonomy.json",
                      "QUESTION_TAGS_PATH": "question_knowledge_tags.json",
                      "QUESTION_ATTRIBUTIONS_PATH": "question_attributions.json"}.items():
        setattr(config, name, str(data / file))
    config.DATA_DIR = str(data)
    config.COURSE_DIR = config.EXAM_DIR = str(private / "no-course-files")
    for flag in ("ENABLE_DENSE_RETRIEVAL", "ENABLE_RERANKING", "ENABLE_QUERY_VARIANTS",
                 "ENABLE_FEATURE_RERANKING", "ENABLE_LEARNED_RANKING", "ENABLE_EVIDENCE_SUBAGENT"):
        setattr(config, flag, False)
    from agents import graph, memory
    import local_load_server as fixture
    graph.route, graph.chitchat = fixture.route, fixture.reply
    memory._save_vector_round = lambda *_: None
    from data_ingest import manifest
    manifest.validate_index = lambda **_: None
    from storage import auth, repos
    sessions = []
    started = time.perf_counter()
    for index in range(8):
        username, password = f"business-user-{index}", secrets.token_urlsafe(24)
        user = auth.register(username, password)
        login = auth.login(username, password)
        assert login is not None
        thread = repos.create_thread(user["id"])
        sessions.append({"session_token": login["session_token"], "csrf_token": login["csrf_token"],
                         "thread_id": thread["id"]})
    (private / "sessions-private.json").write_text(json.dumps(sessions), encoding="utf-8")
    (output / "setup.json").write_text(json.dumps({"users": 8, "seconds": time.perf_counter() - started,
        "password_calls_before_load": 16, "runtime_pid": os.getpid()}), encoding="utf-8")
    import server
    import uvicorn
    from quiz_profile import install
    install(output)
    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
