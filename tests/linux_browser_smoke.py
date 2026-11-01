"""Run key existing browser checks against a fresh synthetic Linux application."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import httpx

from linux_runtime_smoke import copy_application, stop, wait_ready


CHECKS = (
    "browser_readiness_e2e.cjs",
    "browser_auth_isolation_e2e.cjs",
    "browser_submission_retry_e2e.cjs",
    "browser_chat_restore_e2e.cjs",
    "browser_trace_e2e.cjs",
    "browser_structured_sources_e2e.cjs",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "work-dir", "node", "playwright-module", "browser-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    source, work = args.source_root.resolve(), args.work_dir.resolve()
    process = None
    summary = {"ok": False, "checks": [], "work_dir": str(work)}
    try:
        protected = (source / "data").resolve()
        if sys.platform != "linux" or work == protected or protected in work.parents or work in protected.parents:
            raise ValueError("Require Linux and a work-dir outside source/data")
        work.mkdir(parents=True, exist_ok=False)
        app = work / "app"
        copy_application(source, app)
        (app / "tests").mkdir()
        test_source = source / "tests"
        if test_source.is_symlink():
            raise ValueError("Browser source directory must not be symlinked")
        for name in (*CHECKS, "browser_auth.cjs"):
            browser_script = test_source / name
            if browser_script.is_symlink() or browser_script.resolve().parent != test_source:
                raise ValueError("Browser source file must stay inside source/tests")
            shutil.copyfile(browser_script, app / "tests" / name)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
               "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONUTF8": "1", "DATABASE_URL": "", "MYSQL_PASSWORD": "",
               "REDIS_URL": "redis://127.0.0.1:1/0", "LLM_API_KEY": "",
               "LLM_BASE_URL": "http://127.0.0.1:1/v1", "DASHSCOPE_API_KEY": "",
               "ARK_API_KEY": "", "ARK_EMBEDDING_API_KEY": "",
               "ENABLE_DENSE_RETRIEVAL": "false", "ENABLE_RERANKING": "false",
               "ENABLE_QUERY_REWRITE": "false", "ENABLE_QUERY_VARIANTS": "false",
               "CISP_PUBLIC_ORIGIN": origin,
               "PLAYWRIGHT_BROWSERS_PATH": str(args.browser_root.resolve() / "browsers"),
               "LD_LIBRARY_PATH": str(args.browser_root.resolve() / "libs/usr/lib/x86_64-linux-gnu")}
        with (work / "prepare.log").open("wb") as log:
            subprocess.run([sys.executable, "-B", "scripts/prepare_demo.py", "--output-dir",
                            str(work / "demo")], cwd=app, env=env, stdout=log, stderr=log,
                           check=True, timeout=120)
        shutil.copytree(work / "demo", app / "data")
        env["CISP_DB_PATH"] = str(app / "data/cisp_qa.db")
        with (work / "server.log").open("ab") as log, \
                httpx.Client(base_url=origin, trust_env=False, timeout=2) as client:
            process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "server:app",
                                        "--host", "127.0.0.1", "--port", str(port)],
                                       cwd=app, env=env, stdout=log, stderr=log)
            wait_ready(client, process)
            for name in CHECKS:
                with (work / f"{name}.log").open("wb") as browser_log:
                    completed = subprocess.run([str(args.node.resolve()), f"tests/{name}",
                                                str(args.playwright_module.resolve()), "", origin],
                                               cwd=app, env=env, stdout=browser_log,
                                               stderr=browser_log, timeout=180, check=False)
                summary["checks"].append({"name": name, "exit_code": completed.returncode})
                if completed.returncode:
                    raise RuntimeError("Browser check failed; inspect retained per-check log")
        summary.update(ok=True, source_data_copied=False, synthetic_demo=True,
                       docker_image_verified=False, full_nine_group_gate=False,
                       scope="server startup readiness and browser auth real HTTP; browser readiness/submission/chat frontend protocol mocks")
        return 0
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        return 1
    finally:
        stop(process)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
