"use strict";
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2] || "playwright");

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.argv[3] ? { executablePath: process.argv[3] } : {}) });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const id = "12345678-1234-1234-1234-123456789abc";
  const attack = '<img src=x onerror="window.traceInjected=true">';
  let account = "a";
  let csrfVersion = 1;
  let mode = "success";
  let gets = 0;
  const record = () => ({ request_id: id, thread_id: `thread-${account}`,
    status: mode === "success" ? "completed" : "running",
    trace: { conversation_commit: mode === "success" ? "confirmed" : "not_confirmed",
      events: [{ stage: "answer", outcome: "completed", elapsed_ms: 20 }],
      model_calls: [{ stage: "answer", requested_model: "requested-model",
        response_model: attack, response_observed: true, status: "response_received",
        started_ms: 2, ended_ms: 12, usage: { prompt_tokens: 0, completion_tokens: null } },
        { stage: "rewrite", usage: { prompt_tokens: true, completion_tokens: -1 } }],
      service_calls: [{ kind: "embedding", requested_model: "embed-request",
        response_model: "embed-receipt", response_observed: true, status: "response_received",
        started_ms: 1, ended_ms: 3,
        usage: { prompt_tokens: null, completion_tokens: 0, total_tokens: 0 } }],
      cost: { amount: null, status: "unknown_unbound_pricing" } },
    recovery: { state: mode === "success" ? "recorded_terminal" : "unconfirmed",
      reason: mode === "success" ? "terminal_observed" : "observation_deadline_elapsed" } });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    let payload = [];
    if (url.pathname === "/api/ready") payload = { status: "ready" };
    else if (url.pathname === "/api/auth/me") payload = {
      user: { id: account, username: account }, csrf_token: `csrf-${account}-${csrfVersion}` };
    else if (url.pathname === "/api/threads") payload = [{ id: `thread-${account}` }, { id: "thread-other" }];
    else if (url.pathname === "/api/diagnostics") payload = { items: [], has_more: false, next_offset: null };
    else if (url.pathname.startsWith("/api/traces/")) {
      gets += 1;
      if (mode === "missing") {
        await route.fulfill({ status: 404, contentType: "application/json",
          body: JSON.stringify({ detail: "Request trace not found" }) });
        return;
      }
      payload = record();
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/chat/stream", async route => {
    const persisted = mode !== "unpersisted";
    if (mode === "http-error") {
      await route.fulfill({ status: 503, headers: { "X-Request-ID": id },
        contentType: "application/json", body: JSON.stringify({ request_id: id,
          trace_persisted: true, message: "Service unavailable" }) });
      return;
    }
    const rows = [{ type: "token", content: "answer" },
      { type: "usage", usage: { prompt_tokens: 9999, completion_tokens: 8888 } }];
    if (mode === "success") rows.push({ type: "trace", request_id: id,
      trace_persisted: persisted, conversation_commit: "confirmed" }, { type: "done" });
    else if (mode !== "truncated") rows.push({ type: "error", request_id: id, trace_persisted: persisted,
      conversation_commit: "not_confirmed", message: "本轮未确认完成" });
    await route.fulfill({ contentType: "text/event-stream", headers: { "X-Request-ID": id },
      body: rows.map(row => `data: ${JSON.stringify(row)}\n\n`).join("") });
  });
  async function select(value = `thread-${account}`) {
    await page.locator("#thread-select").selectOption(value);
    await page.waitForFunction(() => !chatHistoryLoading && !document.getElementById("chat-send").disabled);
  }
  async function send() {
    await page.locator("#chat-input").fill("trace question");
    await page.locator("#chat-send").click();
    await page.waitForFunction(() => !document.getElementById("chat-send").disabled);
  }
  async function expand() {
    await page.locator("#request-trace summary").click();
    await page.waitForFunction(() => {
      const text = document.getElementById("request-trace-content").textContent;
      return text && text !== "正在读取持久记录…"
        && !document.getElementById("request-trace-refresh").disabled;
    });
  }
  async function holdTrace() {
    await page.evaluate(() => {
      const original = window.fetch;
      window.fetch = (...args) => {
        if (String(args[0]).includes("/api/traces/")) {
          window.fetch = original;
          return new Promise(resolve => { window.releaseTrace = () => resolve(new Response(
            JSON.stringify({ request_id: "12345678-1234-1234-1234-123456789abc",
              thread_id: "thread-a", trace: { conversation_commit: "late-secret" } }),
            { headers: { "Content-Type": "application/json" } })); });
        }
        return original(...args);
      };
    });
    await page.locator("#request-trace-refresh").click();
    await page.waitForFunction(() => typeof window.releaseTrace === "function");
  }
  async function releaseAndCheck() {
    await page.evaluate(async () => { window.releaseTrace(); delete window.releaseTrace;
      await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(await page.locator("#request-trace").isVisible(), false);
    assert.equal(await page.locator("#request-trace-content").textContent(), "");
    assert.doesNotMatch(await page.locator("body").innerText(), /late-secret/);
  }
  try {
    await page.goto(process.argv[4] || "http://127.0.0.1:9530");
    await select();
    await send();
    assert.equal(gets, 0, "SSE must not automatically poll traces");
    assert.match(await page.locator("#request-trace-status").textContent(), /12345678.*已持久化/s);
    await expand();
    const text = await page.locator("#request-trace-content").innerText();
    assert.match(text, /prompt_tokens：0；completion_tokens：未知/);
    assert.match(text, /prompt_tokens：未知；completion_tokens：0；total_tokens（此服务回执）：0/);
    assert.match(text, /prompt_tokens：未知；completion_tokens：未知/);
    assert.match(text, /费用：未知/);
    assert.match(text, /SQL conversation_commit：confirmed/);
    assert.match(text, /回执 model：<img/);
    assert.doesNotMatch(text, /9999|8888/);
    assert.equal(await page.locator("#request-trace-content img").count(), 0);
    assert.equal(await page.evaluate(() => window.traceInjected), undefined);
    if (process.argv[5]) {
      await page.locator("#request-trace").scrollIntoViewIfNeeded();
      await page.screenshot({ path: process.argv[5], fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: process.argv[5].replace(/(\.[^.]+)$/, "-narrow$1"), fullPage: true });
      await page.setViewportSize({ width: 1280, height: 720 });
    }
    for (const nextMode of ["failed", "http-error", "missing", "unpersisted", "truncated"]) {
      mode = nextMode;
      await send();
      if (mode === "http-error") assert.match(await page.locator("#chat-history").innerText(), /HTTP 503/);
      if (mode === "unpersisted") assert.match(await page.locator("#request-trace-status").textContent(), /不能作为可恢复记录/);
      if (mode === "truncated") {
        assert.match(await page.locator("#chat-history").innerText(), /未确认保存/);
        assert.match(await page.locator("#request-trace-status").textContent(), /持久化状态未知/);
      }
      await expand();
      const detail = await page.locator("#request-trace-content").innerText();
      if (mode === "missing") assert.match(detail, /404.*不能据此证明/s);
      else assert.match(detail, /unconfirmed：无法证明执行已停止、已回滚或可以安全重发/);
      if (mode === "unpersisted") assert.match(detail, /此前保存的观察快照，不能证明本轮终态已保存/);
    }
    mode = "success";
    await send();
    await expand();
    await holdTrace();
    await send();
    await page.evaluate(async () => { window.releaseTrace(); delete window.releaseTrace;
      await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(await page.locator("#request-trace-content").textContent(), "");
    assert.doesNotMatch(await page.locator("body").innerText(), /late-secret/);
    await expand();
    await holdTrace();
    csrfVersion += 1;
    await page.evaluate(() => verifyIdentity());
    await select();
    await releaseAndCheck();
    await send();
    await expand();
    await holdTrace();
    await select("thread-other");
    await releaseAndCheck();
    await select("thread-a");
    await send();
    await expand();
    await holdTrace();
    account = "b";
    await page.evaluate(() => verifyIdentity(true));
    await select();
    await releaseAndCheck();
    await send();
    await expand();
    await holdTrace();
    await page.locator("#auth-logout").click();
    await page.locator("#auth-form").waitFor({ state: "visible" });
    await releaseAndCheck();
    assert.deepEqual(errors, []);
    console.log("trace browser protocol mock passed: success/error, receipts, unknown cost, recovery, 404, identity/thread/logout isolation, safe text");
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
