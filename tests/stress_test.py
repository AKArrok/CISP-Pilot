# -*- coding: utf-8 -*-
"""并发压测：N 路并发问答（SSE）+ 并发刷题闭环，统计成功率/延迟/错误。"""
import sys
import threading
import time
import urllib.error
from uuid import uuid4

from live_http_auth import LiveAuth

AUTH = None
results = []
lock = threading.Lock()


def timed(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        ok, extra = True, detail
    except urllib.error.HTTPError as e:
        ok, extra = False, f"HTTP {e.code}"
    except Exception as e:
        ok, extra = False, type(e).__name__ + ":" + str(e)[:60]
    dt = round(time.time() - t0, 1)
    with lock:
        results.append((name, ok, dt, extra))
    print(f"  {'✅' if ok else '❌'} {name:24s} {dt:6.1f}s {extra}")


def chat_tokens(body, timeout=600):
    n = 0
    with AUTH.request("POST", "/chat/stream", body, timeout) as r:
        for line in r:
            if line.startswith(b"data: ") and b'"token"' in line:
                n += 1
    return f"tokens={n}"


def quiz_round(mode, thread_id, timeout=120):
    q = AUTH.post_json("/quiz/next", {"mode": mode, "thread_id": thread_id}, timeout)
    r = AUTH.post_json(
        "/quiz/answer", {"question_id": q["id"], "choice": "A", "thread_id": thread_id, "submission_id": uuid4().hex}, timeout,
    )
    return f"{q['domain']} correct={r['correct']}"


def run(n_chat=12, n_quiz=10):
    owned_threads = [AUTH.new_thread() for _ in range(n_chat + n_quiz)]
    print(f"── {n_chat} 路并发问答 ──")
    threads = []
    for i in range(n_chat):
        thread_id = owned_threads[i]
        if i % 4 == 0:
            body, name = ({"query": f"防火墙的部署位置有哪些（压测{i}）？", "thread_id": thread_id}, f"chat-知识{i}")
        elif i % 4 == 1:
            body, name = ({"query": "谢谢", "thread_id": thread_id}, f"chat-闲聊{i}")
        elif i % 4 == 2:
            body, name = ({"query": "考考我密码学", "thread_id": thread_id}, f"chat-做题{i}")
        else:
            body, name = ({"query": "什么是深度防御？（同题并发）", "thread_id": thread_id}, f"chat-同题{i}")
        threads.append(threading.Thread(target=timed, args=(name, lambda b=body: chat_tokens(b))))
    print(f"── {n_quiz} 路并发刷题 ──")
    for i in range(n_quiz):
        mode = "random" if i % 2 == 0 else "weak"
        thread_id = owned_threads[n_chat + i]
        threads.append(threading.Thread(target=timed,
                       args=(f"quiz-{mode}{i}", lambda m=mode, t=thread_id: quiz_round(m, t))))
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(900)
    wall = round(time.time() - t0, 1)
    ok = sum(1 for _, o, _, _ in results if o)
    dts = sorted(d for _, _, d, _ in results)
    print(f"\n总请求 {len(results)}, 成功 {ok}, 失败 {len(results)-ok}, 墙钟 {wall}s")
    print(f"单请求耗时: min={dts[0]}s p50={dts[len(dts)//2]}s max={dts[-1]}s")


def main(n_chat=12, n_quiz=10):
    global AUTH
    with LiveAuth() as AUTH:
        run(n_chat, n_quiz)


if __name__ == "__main__":
    sys.exit(main())
