"use strict";

const assert = require("node:assert/strict");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);

const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:9530";

async function register(page, suffix) {
  const username = `isolation${suffix}${process.pid}${Date.now()}`;
  const password = `BrowserTest!${username}`;
  await page.locator("#auth-username").fill(username);
  await page.locator("#auth-password").fill(password);
  await page.locator("#auth-register").click();
  await page.locator("#auth-status").filter({ hasText: "登录成功" }).waitFor({ timeout: 30_000 });
  await page.locator("#thread-create").click();
  await page.waitForFunction(() => !!document.querySelector("#thread-select")?.value);
  return { username, password, threadId: await page.locator("#thread-select").inputValue() };
}

async function main() {
  const launchOptions = { headless: true };
  const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
  if (executablePath) launchOptions.executablePath = executablePath;
  const browser = await chromium.launch(launchOptions);
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const pageErrors = [];
  for (const page of [pageA, pageB]) page.on("pageerror", error => pageErrors.push(error.message));

  try {
    await Promise.all([
      pageA.goto(baseUrl, { waitUntil: "networkidle" }),
      pageB.goto(baseUrl, { waitUntil: "networkidle" }),
    ]);
    const accountA = await register(pageA, "a");
    const accountB = await register(pageB, "b");
    assert.notEqual(accountA.threadId, accountB.threadId);

    const denied = await pageB.evaluate(async foreignThreadId => {
      const csrf = csrfToken;
      const requests = [
        fetch(`/api/history?thread_id=${encodeURIComponent(foreignThreadId)}`),
        fetch(`/api/stats?thread_id=${encodeURIComponent(foreignThreadId)}`),
        fetch("/quiz/next", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({ mode: "random", domain: null, thread_id: foreignThreadId }),
        }),
      ];
      return Promise.all(requests.map(async request => (await request).status));
    }, accountA.threadId);
    assert.deepEqual(denied, [404, 404, 404], "B 不能读写 A 的线程");
    const bThreadIds = await pageB.locator("#thread-select option").evaluateAll(options =>
      options.map(option => option.value).filter(Boolean));
    assert.deepEqual(bThreadIds, [accountB.threadId]);

    const sessionCookie = (await contextA.cookies(baseUrl))
      .find(cookie => cookie.name === "cisp_session");
    assert.ok(sessionCookie, "A 登录后应收到 HttpOnly 会话 Cookie");
    assert.equal(sessionCookie.httpOnly, true);

    let releaseStats;
    const statsGate = new Promise(resolve => { releaseStats = resolve; });
    let statsStarted;
    const started = new Promise(resolve => { statsStarted = resolve; });
    let statsFinished;
    const finished = new Promise(resolve => { statsFinished = resolve; });
    await pageA.route("**/api/stats?*", async route => {
      statsStarted();
      await statsGate;
      try {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ total_attempts: 999, domains: [], knowledge_points: [] }) });
      } catch (_) { /* 退出时中止旧请求属于预期行为 */ }
      statsFinished();
    });
    await pageA.evaluate(() => {
      addBubble("user", "A 的私人内容");
      document.querySelector("#stats-summary").textContent = "A 的私人画像";
    });
    await pageA.locator("#tab-quiz").click();
    await started;
    await pageA.locator("#auth-logout").click();
    await pageA.locator("#auth-register").waitFor({ state: "visible" });
    assert.equal(await pageA.locator("#chat-send").isDisabled(), true);
    assert.equal(await pageA.locator("#chat-history").innerText(), "");
    assert.equal(await pageA.locator("#stats-summary").innerText(), "");
    assert.equal(await pageA.locator("#thread-select option").count(), 0);

    const staleContext = await browser.newContext();
    await staleContext.addCookies([sessionCookie]);
    const staleResponse = await staleContext.request.get(`${baseUrl}/api/auth/me`);
    assert.equal(staleResponse.status(), 401, "注销后的旧 Cookie 必须失效");
    await staleContext.close();

    await pageA.locator("#auth-username").fill(accountB.username);
    await pageA.locator("#auth-password").fill(accountB.password);
    await pageA.locator("#auth-login").click();
    await pageA.locator("#auth-status").filter({ hasText: "登录成功" }).waitFor();
    const switchedIds = await pageA.locator("#thread-select option").evaluateAll(options =>
      options.map(option => option.value).filter(Boolean));
    assert.deepEqual(switchedIds, [accountB.threadId], "账号切换后只展示 B 的线程");
    await pageA.locator("#thread-select").selectOption(accountB.threadId);
    releaseStats();
    await finished;
    assert.equal(await pageA.locator("#chat-history").innerText(), "");
    assert.equal(await pageA.locator("#stats-summary").innerText(), "");
    assert.equal(await pageA.locator("#attribution-table tbody tr").count(), 0);
    await pageA.unroute("**/api/stats?*");
    await contextA.clearCookies();
    await pageA.evaluate(() => loadStats());
    await pageA.locator("#auth-status").filter({ hasText: "登录已失效" }).waitFor();
    assert.equal(await pageA.locator("#chat-send").isDisabled(), true);
    assert.equal(await pageA.locator("#thread-select option").count(), 0);
    assert.equal(await pageA.locator("#stats-summary").innerText(), "");

    const sharedContext = await browser.newContext();
    const sharedA = await sharedContext.newPage();
    sharedA.on("pageerror", error => pageErrors.push(error.message));
    await sharedA.goto(baseUrl, { waitUntil: "networkidle" });
    await sharedA.locator("#auth-username").fill(accountA.username);
    await sharedA.locator("#auth-password").fill(accountA.password);
    await sharedA.locator("#auth-login").click();
    await sharedA.locator("#auth-status").filter({ hasText: "登录成功" }).waitFor();
    await sharedA.locator("#thread-select").selectOption(accountA.threadId);
    const sharedB = await sharedContext.newPage();
    sharedB.on("pageerror", error => pageErrors.push(error.message));
    await sharedB.goto(baseUrl, { waitUntil: "networkidle" });
    await sharedB.locator("#account-name").filter({ hasText: accountA.username }).waitFor();

    let releaseSharedStats;
    const sharedStatsGate = new Promise(resolve => { releaseSharedStats = resolve; });
    let sharedStatsStarted;
    const sharedStarted = new Promise(resolve => { sharedStatsStarted = resolve; });
    let sharedStatsFinished;
    const sharedFinished = new Promise(resolve => { sharedStatsFinished = resolve; });
    await sharedA.route("**/api/stats?*", async route => {
      sharedStatsStarted();
      await sharedStatsGate;
      try {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ total_attempts: 999, domains: [], knowledge_points: [] }) });
      } catch (_) { /* 跨标签页切换时旧请求可能被中止 */ }
      sharedStatsFinished();
    });
    await sharedA.evaluate(() => {
      addBubble("user", "跨标签页的 A 私人内容");
      document.querySelector("#stats-summary").textContent = "跨标签页的 A 私人画像";
    });
    await sharedA.locator("#tab-quiz").click();
    await sharedStarted;
    await sharedB.locator("#auth-logout").click();
    await sharedB.locator("#auth-register").waitFor({ state: "visible" });
    await sharedA.locator("#auth-register").waitFor({ state: "visible" });
    await sharedB.locator("#auth-username").fill(accountB.username);
    await sharedB.locator("#auth-password").fill(accountB.password);
    await sharedB.locator("#auth-login").click();
    await sharedB.locator("#auth-status").filter({ hasText: "登录成功" }).waitFor();
    await sharedA.locator("#account-name").filter({ hasText: accountB.username }).waitFor();
    await sharedA.waitForFunction(expected =>
      [...document.querySelectorAll("#thread-select option")].some(option => option.value === expected),
    accountB.threadId);
    assert.deepEqual(await sharedA.locator("#thread-select option").evaluateAll(options =>
      options.map(option => option.value).filter(Boolean)), [accountB.threadId]);
    assert.equal(await sharedA.locator("#thread-select").inputValue(), "",
      "广播切换后不得自动选择旧线程");
    releaseSharedStats();
    await sharedFinished;
    assert.equal(await sharedA.locator("#chat-history").innerText(), "");
    assert.equal(await sharedA.locator("#stats-summary").innerText(), "");
    assert.equal(await sharedA.locator("#attribution-table tbody tr").count(), 0);

    await sharedA.locator("#thread-select").selectOption(accountB.threadId);
    await sharedA.waitForFunction(() => !chatHistoryLoading);
    await sharedA.evaluate(() => {
      addBubble("user", "B 的焦点复核保留内容");
    });
    const unchangedFocusResponse = sharedA.waitForResponse(response =>
      response.url().endsWith("/api/auth/me"));
    await sharedA.evaluate(() => window.dispatchEvent(new Event("focus")));
    await unchangedFocusResponse;
    await sharedA.waitForLoadState("networkidle");
    assert.equal(await sharedA.locator("#thread-select").inputValue(), accountB.threadId,
      "身份未变时焦点复核应保留已选线程");
    assert.match(await sharedA.locator("#chat-history").innerText(), /B 的焦点复核保留内容/);

    const directLoginStatus = await sharedB.evaluate(async credentials => {
      const response = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
      return response.status;
    }, { username: accountA.username, password: accountA.password });
    assert.equal(directLoginStatus, 200, "无前端广播的直接登录应能改变共享 Cookie");
    await sharedA.evaluate(() => window.dispatchEvent(new Event("focus")));
    await sharedA.locator("#account-name").filter({ hasText: accountA.username }).waitFor();
    await sharedA.waitForFunction(expected =>
      [...document.querySelectorAll("#thread-select option")].some(option => option.value === expected),
    accountA.threadId);
    assert.equal(await sharedA.locator("#thread-select").inputValue(), "");
    assert.equal(await sharedA.locator("#chat-history").innerText(), "");
    assert.deepEqual(await sharedA.locator("#thread-select option").evaluateAll(options =>
      options.map(option => option.value).filter(Boolean)), [accountA.threadId]);
    await sharedContext.close();
    assert.deepEqual(pageErrors, []);
    console.log("auth isolation browser e2e passed: ownership, logout, expiry, shared tabs, focus recheck");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
