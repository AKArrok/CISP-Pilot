"use strict";
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2] || "playwright");

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.argv[3] ? { executablePath: process.argv[3] } : {}) });
  const page = await browser.newPage();
  let account = "a";
  let responseMode = "error";
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const histories = {
    a: [{ role: "user", content: "old question" }, { role: "assistant", content: "old answer" }],
    b: [{ role: "user", content: "B question" }, { role: "assistant", content: "B answer" }],
  };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    let payload = [];
    if (url.pathname === "/api/ready") payload = { status: "ready" };
    else if (url.pathname === "/api/auth/me") payload = {
      user: { id: account, username: account }, csrf_token: `csrf-${account}`,
    };
    else if (url.pathname === "/api/threads") payload = [{ id: `thread-${account}` }];
    else if (url.pathname === "/api/diagnostics") payload = { items: [], has_more: false, next_offset: null };
    else if (url.pathname === "/api/history") {
      assert.equal(url.searchParams.get("thread_id"), `thread-${account}`);
      payload = histories[account];
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/chat/stream", async route => {
    const query = route.request().postDataJSON().query;
    const rows = [{ type: "token", content: `reply:${query}` }];
    if (responseMode === "error") rows.push({ type: "error", message: "保存失败" });
    if (responseMode === "done") {
      histories.a.push({ role: "user", content: query }, { role: "assistant", content: `reply:${query}` });
      rows.push({ type: "done" });
    }
    await route.fulfill({ contentType: "text/event-stream",
      body: rows.map(row => `data: ${JSON.stringify(row)}\n\n`).join("") });
  });
  async function select() {
    await page.locator("#thread-select").selectOption(`thread-${account}`);
    await page.waitForFunction(() => !chatHistoryLoading && !document.getElementById("chat-send").disabled);
  }
  async function send(query) {
    await page.locator("#chat-input").fill(query);
    await page.locator("#chat-send").click();
    await page.waitForFunction(() => !document.getElementById("chat-send").disabled);
  }
  try {
    await page.goto(process.argv[4] || "http://127.0.0.1:9530");
    await select();
    assert.match(await page.locator("#chat-history").innerText(), /old answer/);
    await send("failed");
    assert.match(await page.locator("#chat-history").innerText(), /保存失败/);
    await page.reload();
    await select();
    assert.doesNotMatch(await page.locator("#chat-history").innerText(), /failed/);
    responseMode = "truncated";
    await send("unfinished");
    assert.match(await page.locator("#chat-history").innerText(), /未确认保存/);
    responseMode = "done";
    await send("saved");
    await page.reload();
    await select();
    assert.match(await page.locator("#chat-history").innerText(), /reply:saved/);
    await page.evaluate(() => {
      const original = window.fetch;
      let held = false;
      window.fetch = (...args) => {
        if (!held && String(args[0]).includes("/api/history")) {
          held = true;
          return new Promise(resolve => { window.releaseHistory = () => resolve(new Response(
            JSON.stringify([{ role: "assistant", content: "late A secret" }]),
            { headers: { "Content-Type": "application/json" } },
          )); });
        }
        return original(...args);
      };
    });
    await page.locator("#thread-select").selectOption("thread-a");
    await page.waitForFunction(() => typeof window.releaseHistory === "function");
    account = "b";
    await page.evaluate(() => verifyIdentity(true));
    await select();
    await page.evaluate(async () => { window.releaseHistory(); await new Promise(resolve => setTimeout(resolve, 0)); });
    const text = await page.locator("#chat-history").innerText();
    assert.match(text, /B answer/);
    assert.doesNotMatch(text, /old answer|reply:saved|late A secret/);
    assert.deepEqual(errors, []);
    console.log("chat restore browser e2e passed: SQL history rendering, save errors, missing done, account isolation");
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
