# -*- coding: utf-8 -*-
"""MySQL 后端端到端冒烟：问答(SSE) / 刷题 / 判分 / 统计 / 指标。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9528"


def post(path, body, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def chat_tokens(body, timeout=300):
    req = urllib.request.Request(BASE + "/chat/stream",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    tokens, events = 0, []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if line.startswith(b"data: "):
                ev = json.loads(line[6:].decode("utf-8"))
                events.append(ev)
                if ev.get("type") == "token":
                    tokens += 1
    return tokens, events


def main():
    thread_id = "mysql-e2e-2"
    # 1. 问答
    tokens, events = chat_tokens({"query": "什么是入侵检测系统", "thread_id": thread_id})
    intents = [e.get("intent") for e in events if e.get("type") == "intent"]
    print("1. 问答: tokens=%d, intent=%s" % (tokens, intents[-1] if intents else "?"))

    # 2. 刷题（random）+ 判分
    q = post("/quiz/next", {"mode": "random", "thread_id": thread_id})
    print("2. 抽题: id=%s domain=%s" % (q["id"], q["domain"]))
    r = post("/quiz/answer", {"question_id": q["id"], "choice": "A", "thread_id": thread_id})
    print("   判分: correct=%s answer=%s 关联课件=%d" %
          (r["correct"], r["answer"], len(r["related_chunks"])))

    # 3. 薄弱强化
    q2 = post("/quiz/next", {"mode": "weak", "thread_id": thread_id})
    print("3. weak 抽题: %s [%s]" % (q2["id"], q2["domain"]))

    # 4. 统计
    s = post_get(f"/api/stats?thread_id={thread_id}")
    print("4. stats: total_attempts=%s weak=%s" % (s["total_attempts"], s["weak_domains"]))

    # 5. 指标
    m = post_get("/api/metrics")
    print("5. metrics: requests=%s by_intent=%s avg_total=%sms" %
          (m["total_requests"], m["by_intent"], m.get("avg_total_ms")))


def post_get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


if __name__ == "__main__":
    sys.exit(main())
