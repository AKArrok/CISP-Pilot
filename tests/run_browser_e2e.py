"""在隔离 SQLite 数据库上启动服务并执行浏览器诊断 E2E。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PORT = 9530


def _wait_until_ready(url: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"测试服务提前退出，exit={process.returncode}")
        try:
            with urllib.request.urlopen(url + "/api/ready", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("等待浏览器 E2E 测试服务超时")


def main() -> int:
    browser_host = os.getenv("CISP_BROWSER_HOST", "127.0.0.1")
    browser_url = f"http://{browser_host}:{PORT}"
    node = os.getenv("CISP_NODE", "node")
    playwright_module = os.getenv("CISP_PLAYWRIGHT_MODULE", "playwright")
    browser_executable = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH", "")

    with tempfile.TemporaryDirectory(prefix="cisp-browser-e2e-") as temp_dir:
        env = os.environ.copy()
        env["CISP_DB_PATH"] = str(Path(temp_dir) / "e2e.db")
        env["DATABASE_URL"] = ""
        env["MYSQL_PASSWORD"] = ""
        # 用测试的是画像闭环，不应让远程 embedding / rerank 额度和网络
        # 成为结果变量。未支持的后端会立即触发已有的 BM25 降级路径。
        env["EMBEDDING_BACKEND"] = "dashscope"
        env["ENABLE_DENSE_RETRIEVAL"] = "false"
        env["ENABLE_RERANKING"] = "false"
        env["ENABLE_QUERY_VARIANTS"] = "false"
        artifacts = (
            ROOT / "data" / "knowledge_taxonomy.json",
            ROOT / "data" / "question_knowledge_tags.json",
            ROOT / "data" / "question_attributions.json",
        )
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        }
        subprocess.run(
            [
                sys.executable, "scripts/build_taxonomy.py",
                "--import-db", "--db-only",
            ],
            cwd=ROOT, env=env, check=True,
        )
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
        )
        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        }
        if after != before:
            raise RuntimeError("隔离浏览器 E2E 不得改写归因构建产物")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0",
             "--port", str(PORT), "--log-level", "warning"],
            cwd=ROOT,
            env=env,
        )
        try:
            _wait_until_ready(f"http://127.0.0.1:{PORT}", server)
            for script in (
                "tests/browser_readiness_e2e.cjs",
                "tests/browser_diagnostic_e2e.cjs",
                "tests/browser_attribution_correction_e2e.cjs",
                "tests/browser_attribution_correction_live_e2e.cjs",
            ):
                completed = subprocess.run(
                    [node, script, playwright_module, browser_executable, browser_url],
                    cwd=ROOT,
                    check=False,
                )
                if completed.returncode:
                    return completed.returncode
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            # Windows may release the SQLite handle a moment after process exit.
            time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
