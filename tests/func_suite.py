# -*- coding: utf-8 -*-
"""功能面回归：多轮追问 / 独立线程 / 刷题闭环 / AI 出题 / 边界输入。"""
import json
import urllib.error
import time
import urllib.parse
from uuid import uuid4

from quiz.generator import generate_question
from live_http_auth import LiveAuth

PASS, FAIL = [], []
AUTH = None


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ✅ " if ok else "  ❌ ") + name + (f" | {detail}" if detail else ""))


def post(path, body, timeout=300):
    return AUTH.request("POST", path, body, timeout)


def post_json(path, body, timeout=300):
    with post(path, body, timeout) as r:
        return json.loads(r.read())


def chat(body, timeout=300):
    tokens, events = 0, []
    with AUTH.request("POST", "/chat/stream", body, timeout) as r:
        for line in r:
            if line.startswith(b"data: "):
                ev = json.loads(line[6:].decode("utf-8"))
                events.append(ev)
                if ev.get("type") == "token":
                    tokens += 1
    return tokens, events


def run():
    print("── A1 多轮追问（含指代消解）──")
    UNIQ = str(int(time.time()))[-5:]  # 问题文本加唯一后缀，避免跨次运行命中缓存
    thread_a = AUTH.new_thread()
    t1, e1 = chat({"query": f"什么是业务影响分析BIA？（备考{UNIQ}）", "thread_id": thread_a})
    ok1 = t1 > 50
    check("首轮知识问答", ok1, f"tokens={t1}")
    t2, e2 = chat({"query": "它和RTO是什么关系？", "thread_id": thread_a})
    ans2 = "".join(e.get("content", "") for e in e2 if e.get("type") == "token")
    check("追问带指代（应答 BIA 与 RTO 关系）", t2 > 50 and "BIA" in ans2, f"tokens={t2}")

    print("── A2 独立线程问答 ──")
    q = "什么是社会工程学"
    chat({"query": q, "thread_id": AUTH.new_thread()})
    t, e = chat({"query": q, "thread_id": AUTH.new_thread()})
    done = [e for e in e if e.get("type") == "done"]
    check("同问题第二线程仍独立生成回答", t > 0 and bool(done) and not done[0].get("cached"), f"tokens={t}")

    print("── A3 刷题闭环 ──")
    quiz_thread = AUTH.new_thread()
    q1 = post_json("/quiz/next", {"mode": "random", "thread_id": quiz_thread})
    check("抽题含题干选项且无答案泄露", "answer" not in q1 and len(q1.get("options", {})) >= 2, q1["id"])
    r1 = post_json("/quiz/answer", {
        "question_id": q1["id"], "choice": "A", "thread_id": quiz_thread, "submission_id": uuid4().hex,
    })
    check("判分返回对错+解析+关联课件", set(r1) >= {"correct", "answer", "analysis", "related_chunks"},
          f"correct={r1['correct']} related={len(r1['related_chunks'])}")
    q2 = post_json("/quiz/next", {"mode": "weak", "thread_id": quiz_thread})
    check("薄弱强化抽题", bool(q2.get("id")), q2.get("domain", ""))
    s = AUTH.get_json(f"/api/stats?thread_id={quiz_thread}")
    check("答题后画像更新", s["total_attempts"] >= 1)

    print("── A4 AI 生成题（真实 LLM）──")
    g = generate_question("信息安全监管")
    check("AI 生成题结构完整且已入库", bool(g and g["answer"] in "ABCD" and len(g["options"]) == 4), g["id"] if g else "None")

    print("── A5 边界输入 ──")
    try:
        post_json("/quiz/answer", {
            "question_id": "no_such", "choice": "A", "thread_id": quiz_thread, "submission_id": uuid4().hex,
        })
        check("不存在题目返回 4xx", False)
    except urllib.error.HTTPError as e:
        check("不存在题目返回 404", e.code == 404)
    try:
        post_json("/quiz/answer", {
            "question_id": q1["id"], "choice": "Z9", "thread_id": quiz_thread, "submission_id": uuid4().hex,
        })
        check("非法选项不崩溃", True)
    except urllib.error.HTTPError as e:
        check("非法选项不崩溃(4xx)", 400 <= e.code < 500)
    try:
        post_json("/chat/stream", {"query": "", "thread_id": AUTH.new_thread()})
        check("空问题被拒绝(422)", False)
    except urllib.error.HTTPError as e:
        check("空问题被拒绝(422)", e.code == 422, str(e.code))
    try:
        post_json("/chat/stream", {"query": "x" * 5000, "thread_id": AUTH.new_thread()})
        check("超长问题被拒绝(422)", False)
    except urllib.error.HTTPError as e:
        check("超长问题被拒绝(422)", e.code == 422, str(e.code))
    try:
        AUTH.get_json("/api/history?" + urllib.parse.urlencode({"thread_id": "不存在"}), timeout=30)
        check("历史查询未知会话返回 404", False)
    except urllib.error.HTTPError as e:
        check("历史查询未知会话返回 404", e.code == 404, str(e.code))

    print(f"\n通过 {len(PASS)}/{len(PASS)+len(FAIL)}")
    if FAIL:
        print("失败项:", FAIL)


def main():
    global AUTH
    with LiveAuth() as AUTH:
        run()


if __name__ == "__main__":
    main()
