"""Start a loopback-only evaluation demo with isolated synthetic application storage."""
import argparse
import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--username", default="eval-reviewer")
    parser.add_argument("--port", type=int, default=9541)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace in {ROOT, ROOT / "data"} or workspace in ROOT.parents or (ROOT / "data") in workspace.parents:
        raise ValueError("Use a dedicated demo directory")
    password = os.environ.get("CISP_EVAL_DEMO_PASSWORD") or getpass.getpass("Demo account password (12–128 chars): ")
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "CISP_DB_PATH": str(workspace / "cisp_qa.db"),
                       "CISP_EVAL_DB_PATH": str(workspace / "platform.sqlite3"), "DATABASE_URL": "", "MYSQL_PASSWORD": "",
                       "CISP_PUBLIC_ORIGIN": f"http://127.0.0.1:{args.port}", "LLM_API_KEY": "", "DASHSCOPE_API_KEY": "",
                       "EMBEDDING_BACKEND": "dashscope", "ENABLE_DENSE_RETRIEVAL": "false", "ENABLE_RERANKING": "false",
                       "ENABLE_QUERY_VARIANTS": "false", "REDIS_URL": "redis://127.0.0.1:1/0"})
    import json

    from scripts.prepare_demo import prepare

    if not workspace.exists():
        prepare(workspace)
        (workspace / ".gitignore").write_text("*\n", encoding="utf-8")
    elif not (workspace / "manifest.json").exists() or not json.loads((workspace / "manifest.json").read_text(encoding="utf-8")).get("synthetic_demo"):
        raise ValueError("Existing workspace must be an explicitly prepared synthetic demo")
    import config

    config.DATA_DIR = str(workspace)
    for attribute, filename in {"KB_CHUNKS_PATH": "kb_chunks.json", "QUESTIONS_PATH": "questions.json",
                                "KNOWLEDGE_TAXONOMY_PATH": "knowledge_taxonomy.json", "QUESTION_TAGS_PATH": "question_knowledge_tags.json",
                                "QUESTION_ATTRIBUTIONS_PATH": "question_attributions.json", "VECTORS_PATH": "vectors.npz",
                                "MEMORY_VECTORS_PATH": "memory_vectors.npz", "PARSE_FAILURES_PATH": "parse_failures.json"}.items():
        setattr(config, attribute, str(workspace / filename))
    from storage import auth, repos

    user = repos.get_user_by_username(args.username.strip().lower())
    if user is None:
        public = auth.register(args.username, password)
    else:
        if not auth.verify_password(password, user["password_hash"]):
            raise ValueError("Existing demo account password does not match")
        public = {"id": user["id"], "username": user["username"]}
    os.environ["CISP_EVAL_USER_IDS"] = str(public["id"])
    from storage.evaluation_workspace import migrate

    migrate()  # Explicit demo bootstrap upgrades its isolated evaluation store only.
    from storage.data_catalog import migrate as migrate_catalog

    migrate_catalog()
    os.environ["CISP_EVAL_WORKER_ENABLED"] = "true"
    os.environ.pop("CISP_EVAL_DEMO_PASSWORD", None)
    print(json.dumps({"url": f"http://127.0.0.1:{args.port}/evaluation.html", "user": public,
                      "workspace": str(workspace), "model_calls": 0}), flush=True)
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
