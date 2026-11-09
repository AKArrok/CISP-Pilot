"""Freeze a fresh isolated replay plus IP-limit/password-work boundary checks."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/engineering-delivery/auth-profile-20261106-r6"
OUTPUT = ROOT / "docs/engineering-delivery/auth-limit-20261109"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=False)
    private = ROOT / "data/review_packets/auth-limit-20261109"
    private.mkdir(exist_ok=False)
    for name in ("driver.py", "server-fixture.py", "locustfile.py"):
        content = (SOURCE / name).read_text(encoding="utf-8").replace("auth-profile-20261106-r6", "auth-limit-20261109")
        if name == "driver.py":
            content = content.replace('def invalid_login(username, label):', 'def invalid_login(username, label, expected_status=401):')
            content = content.replace('assert status == 401', 'assert status == expected_status')
            anchor = '            invalid_login("nonexistent-profile-user", "unknown-account-warm-cache")'
            assert content.count(anchor) == 1
            content = content.replace(anchor, anchor + '''
            invalid_login("nonexistent-profile-user", "ip-attempt29-allowed")
            invalid_login("nonexistent-profile-user", "ip-attempt30-allowed")
            trace_path = OUTPUT / "scrypt.jsonl"
            count_before = len(trace_path.read_text(encoding="utf-8").splitlines())
            invalid_login("nonexistent-profile-user", "ip-attempt31-rejected", expected_status=429)
            count_after = len(trace_path.read_text(encoding="utf-8").splitlines())
            boundary = {"status": negatives[-1]["status"], "derive_before": count_before,
                "derive_after": count_after, "no_password_work_on_rejection": count_before == count_after}
            (OUTPUT / "limit-boundary.json").write_text(json.dumps(boundary, indent=2)+"\\n", encoding="utf-8")
            assert count_before == count_after
''')
        (OUTPUT / name).write_text(content, encoding="utf-8")
    (OUTPUT / "application-server.py").write_bytes((ROOT / "server.py").read_bytes())
    (OUTPUT / ".gitignore").write_text("server.log\n__pycache__/\n", encoding="utf-8")
    plan = json.loads((SOURCE / "plan.json").read_text(encoding="utf-8"))
    plan.update(base_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        scope="Current auth replay plus IP attempt31 rejection before scrypt; synthetic isolated SQLite, real TCP/Locust, no model calls",
        expected_login_attempts=31, negative_cases=["4 invalid credentials401", "attempt31 IP limit429 without password derivation"],
        candidate="No production auth change: validate original cost/limits and complete load harness coverage",
        previous_failure="No new failed run yet; old results preserved, current replay not assumed passed",
        gates=["26 login/me/logout/revoked-cookie cycles", "4 invalid credential401", "attempt31 returns429 with no new scrypt trace",
               "all health checks200", "original scrypt parameters and concurrency2", "defaultDB unchanged", "owned children terminated"])
    plan["default_db_before"] = sha(ROOT / "data/cisp_qa.db")
    assert plan["default_db_before"] == "3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62"
    plan["frozen_sha256"] = {name: sha(OUTPUT / name) for name in plan["frozen_sha256"]}
    plan["runtime_sha256"] = {name: sha(ROOT / name) for name in plan["runtime_sha256"]}
    (OUTPUT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text("# 当前认证隔离回归与限流成本边界\n\n已冻结，尚未执行。不修改密码强度、并发容量或限流门限。继承r6真实TCP/Locust 1/4/8用户及会话撤销检查，增加第29/30次允许计算与第31次429零计算门禁。保留旧CSV和原生最终JSON，不将短样本解释为生产容量。账号、口令和数据库留在忽略的data隔离目录。\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "calls": 0, "planned_auth_attempts": 31}))
