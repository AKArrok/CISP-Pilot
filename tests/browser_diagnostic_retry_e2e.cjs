"use strict";

const assert = require("node:assert/strict");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);
const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:9530";

async function main() {
  const launchOptions = { headless: true };
  const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
  if (executablePath) launchOptions.executablePath = executablePath;
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  const accounts = {
    a: { user: { id: "account-a", username: "AccountA" }, csrf_token: "csrf-a" },
    b: { user: { id: "account-b", username: "AccountB" }, csrf_token: "csrf-b" },
  };
  let account = "a";
  let failStart = true;
  let failNext = true;
  let failAnswer = true;
  let holdStart = false;
  let releaseHeldStart;
  let heldStartSeen;
  const heldStartReady = new Promise(resolve => { heldStartSeen = resolve; });
  const starts = [];
  const answers = [];
  const sessions = new Map();
  const ledger = new Map();
  const presentations = new Map();
  const nextRequests = [];
  const question = {
    id: "diagnostic-q1", stem: "诊断题一", domain: "domain-a", source: "mock",
    options: { A: "选项甲", B: "选项乙" },
  };
  const nextQuestion = { ...question, id: "diagnostic-q2", stem: "诊断题二" };
  const reply = (route, payload, status = 200) => route.fulfill({
    status, contentType: "application/json", body: JSON.stringify(payload),
  });
  await page.route("**/api/**", route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/ready") return reply(route, { status: "ready" });
    if (pathname === "/api/auth/me") return reply(route, accounts[account]);
    if (pathname === "/api/threads") return reply(route, [{ id: `thread-${account}` }]);
    if (pathname === "/api/history") return reply(route, []);
    if (pathname === "/api/domains") return reply(route, ["domain-a"]);
    return reply(route, { items: [], total: 0, has_more: false, next_cursor: null });
  });
  await page.route("**/diagnostic/start", async route => {
    const payload = route.request().postDataJSON();
    starts.push(payload);
    assert.match(payload.start_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    const prior = sessions.get(payload.start_id);
    if (prior) assert.deepEqual(payload, prior.payload, "重试必须复用完整启动参数");
    else sessions.set(payload.start_id, {
      payload, result: { session_id: `session-${sessions.size + 1}` },
    });
    if (holdStart) {
      heldStartSeen();
      await new Promise(resolve => { releaseHeldStart = resolve; });
      try { return await reply(route, sessions.get(payload.start_id).result); }
      catch (_) { return; } // 客户端切换身份后可能已中止请求。
    }
    if (failStart) {
      failStart = false;
      return reply(route, { detail: "启动响应丢失" }, 503);
    }
    return reply(route, sessions.get(payload.start_id).result);
  });
  await page.route("**/diagnostic/*/next", route => {
    const sessionId = new URL(route.request().url()).pathname.split("/")[2];
    const answered = ledger.has(`${sessionId}:diagnostic-q1`);
    const selected = answered ? nextQuestion : question;
    nextRequests.push({ sessionId, questionId: selected.id });
    presentations.set(`${sessionId}:${selected.id}`, selected);
    if (failNext) {
      failNext = false;
      return reply(route, { detail: "出题响应丢失" }, 503);
    }
    return reply(route, {
      status: "active", question: selected,
      progress: { answered: answered ? 1 : 0, max_questions: 6 },
    });
  });
  await page.route("**/diagnostic/*/answer", route => {
    const sessionId = new URL(route.request().url()).pathname.split("/")[2];
    const payload = route.request().postDataJSON();
    answers.push({ sessionId, payload });
    const key = `${sessionId}:${payload.question_id}`;
    const prior = ledger.get(key);
    if (prior) assert.deepEqual(payload, prior.payload, "诊断重试必须复用选择、信心与理由");
    else ledger.set(key, {
      payload, result: { status: "active", result: {
        correct: payload.choice === "A", answer: "A", analysis: "一次判分的解析",
        related_chunks: [],
      } },
    });
    if (failAnswer) {
      failAnswer = false;
      return reply(route, { detail: "判分响应丢失" }, 503);
    }
    return reply(route, ledger.get(key).result);
  });

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator("#thread-select").selectOption("thread-a");
    await page.locator("#tab-quiz").click();
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.locator("#diagnostic-domains option").first().waitFor();
    await page.locator("#diagnostic-domains").selectOption(["domain-a"]);
    await page.locator("#diagnostic-goal").fill("初次诊断");
    await page.locator("#diagnostic-start").click();
    await page.locator("#diagnostic-hint").filter({ hasText: "诊断启动失败" }).waitFor();
    assert.equal(starts.length, 1);
    assert.equal(sessions.size, 1, "首次请求虽返回 503，服务端已建立会话");
    await page.locator("#diagnostic-start").click();
    await page.locator("#diagnostic-next").waitFor({ state: "visible" });
    assert.deepEqual(starts[1], starts[0]);
    assert.equal(sessions.size, 1, "启动重试只建立一个逻辑会话");
    assert.equal(presentations.size, 1, "首次出题响应失败前已选定题目");
    await page.locator("#diagnostic-next").click();
    await page.locator("#q-options .option").first().waitFor();
    assert.deepEqual(nextRequests[1], nextRequests[0]);
    assert.equal(presentations.size, 1, "出题重试复用同一会话的同一题");

    await page.locator("#diagnostic-confidence").selectOption("3");
    await page.locator("#diagnostic-reason").fill("首次提交理由");
    await page.locator("#q-options .option").first().click();
    await page.locator("#q-stem").filter({ hasText: "请点击原选项重试" }).waitFor();
    assert.equal(await page.locator("#q-options .option").first().evaluate(el => el.classList.contains("locked")), false);
    assert.equal(ledger.size, 1, "首次判分虽返回 503，服务端已入账");
    await page.locator("#diagnostic-confidence").selectOption("0");
    await page.locator("#diagnostic-reason").fill("编辑后的理由不得发送");
    await page.evaluate(() => choose("B", document.querySelectorAll("#q-options .option")[1]));
    assert.equal(answers.length, 1, "失败后不能改选项并复用同一判分请求");
    await page.locator("#q-options .option").first().click();
    await page.locator("#q-options .option").filter({ hasText: "选项甲" }).first().waitFor();
    await page.locator("#q-stem").filter({ hasText: "诊断题二" }).waitFor();
    assert.equal(answers.length, 2);
    assert.deepEqual(answers[1], answers[0]);
    assert.equal(ledger.size, 1, "判分重试只产生一条逻辑记录");

    failStart = true;
    await page.locator("#diagnostic-start").click();
    await page.locator("#diagnostic-hint").filter({ hasText: "诊断启动失败" }).waitFor();
    const abandoned = starts.at(-1);
    await page.locator("#diagnostic-goal").fill("变更后的新诊断");
    await page.locator("#diagnostic-start").filter({ hasText: "放弃上次请求" }).waitFor();
    await page.locator("#diagnostic-start").click();
    await page.locator("#q-stem").filter({ hasText: "诊断题一" }).waitFor();
    assert.notEqual(starts.at(-1).start_id, abandoned.start_id);
    assert.equal(starts.at(-1).self_report.goal, "变更后的新诊断");

    holdStart = true;
    await page.locator("#diagnostic-start").click();
    await heldStartReady;
    const oldAccountStart = starts.at(-1);
    account = "b";
    await page.evaluate(() => verifyIdentity(true));
    releaseHeldStart();
    await page.waitForLoadState("networkidle");
    assert.equal(await page.evaluate(() => diagnosticStart), null);
    assert.equal(await page.locator("#thread-select").inputValue(), "");
    assert.equal(await page.locator("#q-options .option").count(), 0);
    holdStart = false;
    await page.locator("#thread-select").selectOption("thread-b");
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.locator("#diagnostic-domains option").first().waitFor();
    await page.locator("#diagnostic-domains").selectOption(["domain-a"]);
    await page.locator("#diagnostic-start").click();
    await page.locator("#q-stem").filter({ hasText: "诊断题一" }).waitFor();
    assert.notEqual(starts.at(-1).start_id, oldAccountStart.start_id);
    assert.equal(starts.at(-1).thread_id, "thread-b");
    assert.equal(await page.evaluate(startId => Object.values(localStorage).some(value =>
      value.includes(startId)), starts.at(-1).start_id), false);
    assert.deepEqual(pageErrors, []);
    console.log("diagnostic retry browser e2e passed: stable retries, new starts, account isolation");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
