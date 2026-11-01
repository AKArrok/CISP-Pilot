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
  let failNext = true;
  const requests = [];
  const ledger = new Map();
  const question = {
    id: "same-question", stem: "同一题目也可以重新练习", domain: "domain-a",
    source: "mock", options: { A: "选项甲", B: "选项乙" },
  };
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
    if (pathname === "/api/stats") return reply(route, { total_attempts: 0 });
    return reply(route, { items: [], total: 0, has_more: false, next_cursor: null });
  });
  await page.route("**/quiz/next", route => reply(route, question));
  await page.route("**/quiz/answer", route => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    assert.match(payload.submission_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    const prior = ledger.get(payload.submission_id);
    if (prior) assert.deepEqual(payload, prior.payload, "重试的完整 payload 必须不变");
    else ledger.set(payload.submission_id, {
      payload, result: { correct: payload.choice === "A", answer: "A", analysis: "一次提交的解析", related_chunks: [] },
    });
    if (failNext) {
      failNext = false;
      // 模拟服务已完成并记账，但代理丢失响应；随后重试返回已有结果。
      return reply(route, { detail: "响应丢失，请重试" }, 503);
    }
    return reply(route, ledger.get(payload.submission_id).result);
  });

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator("#thread-select").selectOption("thread-a");
    await page.locator("#tab-quiz").click();
    await page.locator("#quiz-next").click();
    await page.locator("#q-options .option").first().waitFor();
    const firstId = await page.evaluate(() => quizSubmission.id);
    await page.locator("#q-options .option").first().click();
    await page.locator("#q-stem").filter({ hasText: "请点击原选项重试" }).waitFor();
    assert.equal(await page.locator("#q-feedback").isVisible(), false);
    assert.equal(requests.length, 1);
    assert.equal(ledger.size, 1, "第一次响应失败前，服务已经完成提交");
    await page.locator("#q-options .option").nth(1).click();
    await page.evaluate(() => choose("B", document.querySelectorAll("#q-options .option")[1]));
    assert.equal(requests.length, 1, "失败后不得改变 choice 复用 submission_id");
    await page.locator("#q-options .option").first().click();
    await page.locator("#q-feedback").waitFor({ state: "visible" });
    assert.equal(requests.length, 2);
    assert.deepEqual(requests[1], requests[0]);
    assert.equal(ledger.size, 1, "网络重试应只产生一个逻辑提交");
    assert.equal(await page.locator("#q-analysis").innerText(), "【解析】一次提交的解析");
    await page.locator("#q-options .option").first().click();
    await page.evaluate(() => choose("A", document.querySelector("#q-options .option")));
    assert.equal(requests.length, 2, "成功后重复点击不再次提交或重复展示结果");

    await page.locator("#quiz-next").click();
    await page.locator("#q-options .option:not(.locked)").first().waitFor();
    const freshId = await page.evaluate(() => quizSubmission.id);
    assert.notEqual(freshId, firstId, "重新展示同一个 question_id 必须生成新提交 ID");
    await page.locator("#q-options .option").nth(1).click();
    await page.locator("#q-feedback").waitFor({ state: "visible" });
    assert.equal(requests[2].submission_id, freshId);
    assert.equal(ledger.size, 2);

    failNext = true;
    await page.locator("#quiz-next").click();
    await page.locator("#q-options .option:not(.locked)").first().waitFor();
    await page.locator("#q-options .option").first().click();
    await page.locator("#q-stem").filter({ hasText: "请点击原选项重试" }).waitFor();
    const abandoned = requests[3];
    await page.evaluate(() => { window.oldOption = document.querySelector("#q-options .option"); });
    account = "b";
    await page.evaluate(() => verifyIdentity(true));
    assert.equal(await page.evaluate(() => quizSubmission), null);
    assert.equal(await page.locator("#thread-select").inputValue(), "",
      "账号切换后不能沿用旧线程");
    assert.equal(await page.locator("#q-options .option").count(), 0);
    await page.locator("#thread-select").selectOption("thread-b");
    await page.locator("#quiz-next").click();
    await page.locator("#q-options .option:not(.locked)").first().waitFor();
    await page.evaluate(() => window.oldOption.onclick());
    assert.equal(requests.length, 4, "保存的旧题选项不能提交到新账号");
    await page.locator("#q-options .option").nth(1).click();
    await page.locator("#q-feedback").waitFor({ state: "visible" });
    assert.notEqual(requests[4].submission_id, abandoned.submission_id);
    assert.equal(requests[4].thread_id, "thread-b");
    assert.equal(requests[4].choice, "B");
    assert.equal(await page.evaluate(() => Object.values(localStorage).some(value =>
      value.includes(quizSubmission.id))), false, "提交 ID 不得进入 localStorage");
    assert.deepEqual(pageErrors, []);
    console.log("submission retry browser e2e passed: stable payload, fresh practice, account isolation");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
