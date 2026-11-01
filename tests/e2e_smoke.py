# -*- coding: utf-8 -*-
"""MySQL 后端端到端冒烟：问答(SSE) / 刷题 / 判分 / 统计 / 指标。"""
import json
import sys
from uuid import uuid4

from live_http_auth import LiveAuth


def post(auth, path, body, timeout=300):
    return auth.post_json(path, body, timeout)


def chat_tokens(auth, body, timeout=300):
    tokens, events = 0, []
    with auth.request("POST", "/chat/stream", body, timeout) as r:
        for line in r:
            if line.startswith(b"data: "):
                ev = json.loads(line[6:].decode("utf-8"))
                events.append(ev)
                if ev.get("type") == "token":
                    tokens += 1
    return tokens, events


def run(auth):
    thread_id = auth.new_thread()
    # 1. 问答
    tokens, events = chat_tokens(auth, {"query": "什么是入侵检测系统", "thread_id": thread_id})
    intents = [e.get("intent") for e in events if e.get("type") == "intent"]
    print("1. 问答: tokens=%d, intent=%s" % (tokens, intents[-1] if intents else "?"))

    # 2. 刷题（random）+ 判分
    q = post(auth, "/quiz/next", {"mode": "random", "thread_id": thread_id})
    print("2. 抽题: id=%s domain=%s" % (q["id"], q["domain"]))
    r = post(auth, "/quiz/answer", {"question_id": q["id"], "choice": "A", "thread_id": thread_id, "submission_id": uuid4().hex})
    print("   判分: correct=%s answer=%s 关联课件=%d" %
          (r["correct"], r["answer"], len(r["related_chunks"])))

    # 3. 薄弱强化
    q2 = post(auth, "/quiz/next", {"mode": "weak", "thread_id": thread_id})
    print("3. weak 抽题: %s [%s]" % (q2["id"], q2["domain"]))

    # 4. 统计
    s = post_get(auth, f"/api/stats?thread_id={thread_id}")
    print("4. stats: total_attempts=%s weak=%s" % (s["total_attempts"], s["weak_domains"]))

    # 5. 指标
    m = post_get(auth, "/api/metrics")
    print("5. metrics: requests=%s by_intent=%s avg_total=%sms" %
          (m["total_requests"], m["by_intent"], m.get("avg_total_ms")))


def post_get(auth, path):
    return auth.get_json(path)


def main():
    with LiveAuth() as auth:
        run(auth)


if __name__ == "__main__":
    sys.exit(main())
