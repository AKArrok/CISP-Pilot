"use strict";
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2] || "playwright");

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.argv[3] ? { executablePath: process.argv[3] } : {}) });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const source = "https://raw.githubusercontent.com/usnistgov/oscal-content/pinned/catalog.json";
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const payload = path === "/api/ready" ? { status: "ready" } :
      path === "/api/auth/me" ? { user: { id: "a", username: "test" }, csrf_token: "csrf-test" } :
      path === "/api/threads" ? [{ id: "thread-test" }] :
      path === "/api/diagnostics" ? { items: [], has_more: false, next_offset: null } : [];
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/chat/stream", async route => {
    const contexts = [{ source: "课件", page: 7 },
      { source: "NIST", page: null, locator: "cp-9", source_url: source },
      { source: "NIST", page: null, locator: "ac-8", source_url: source },
      { source: "<img src=x onerror=alert(1)>", page: null, locator: "at-2", source_url: "javascript:alert(1)" }];
    const rows = [{ type: "contexts", contexts }, { type: "token", content: "结构化来源演示。" }, { type: "done" }];
    await route.fulfill({ contentType: "text/event-stream",
      body: rows.map(row => `data: ${JSON.stringify(row)}\n\n`).join("") });
  });
  try {
    await page.goto(process.argv[4] || "http://127.0.0.1:9530");
    await page.locator("#thread-select").selectOption("thread-test");
    await page.waitForFunction(() => !chatHistoryLoading && !document.getElementById("chat-send").disabled);
    await page.locator("#chat-input").fill("系统备份和访问告知有哪些来源？");
    await page.locator("#chat-send").click();
    await page.waitForFunction(() => !document.getElementById("chat-send").disabled);
    const links = page.locator("#chat-history a");
    assert.equal(await links.count(), 4);
    assert.match(await links.nth(0).getAttribute("href"), /page=7#page=7/);
    assert.equal(await links.nth(1).innerText(), "《NIST》定位【cp-9】 ↗");
    assert.equal(await links.nth(2).innerText(), "《NIST》定位【ac-8】 ↗");
    assert.equal(await links.nth(1).getAttribute("href"), source);
    assert.equal(await links.nth(3).getAttribute("href"), null);
    assert.equal(await page.locator("#chat-history img").count(), 0);
    assert.deepEqual(errors, []);
    if (process.env.CISP_SOURCE_SCREENSHOT) await page.screenshot({ path: process.env.CISP_SOURCE_SCREENSHOT, fullPage: true });
    console.log("structured sources browser passed: PDF, distinct controls, safe links and escaped text; mocked HTTP protocol");
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
