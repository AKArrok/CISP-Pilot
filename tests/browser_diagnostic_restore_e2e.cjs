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
  let failListOffset = null;
  const starts = [];
  const nextRequests = [];
  const reportRequests = [];
  const listRequests = [];
  const entry = (sessionId, status = "active") => ({
    session_id: sessionId, status, selected_domains: ["domain-a"],
    progress: { answered: status === "completed" ? 6 : 2, max_questions: 6 },
    created_at: 1791273600, updated_at: 1791274200,
  });
  const records = [entry("active-a"), entry("completed-a", "completed"),
    ...Array.from({ length: 20 }, (_, index) => entry(`older-a-${index}`))];
  const question = {
    id: "pending-a-question", stem: "A 的未提交诊断题", domain: "domain-a", source: "mock",
    options: { A: "选项甲", B: "选项乙" },
  };
  const frozenReport = {
    weak_domains: ["domain-a"], recommendations: ["冻结报告建议"],
    domains: [{ domain: "domain-a", attempts: 6, mastery: 0.25, confidence: 0.75, weak: true }],
    knowledge_points: [],
  };
  const reply = (route, payload, status = 200) => route.fulfill({
    status, contentType: "application/json", body: JSON.stringify(payload),
  });
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/ready") return reply(route, { status: "ready" });
    if (url.pathname === "/api/auth/me") return reply(route, accounts[account]);
    if (url.pathname === "/api/threads") return reply(route, [
      { id: `thread-${account}` }, { id: `empty-thread-${account}` },
    ]);
    if (url.pathname === "/api/domains") return reply(route, ["domain-a"]);
    if (url.pathname === "/api/history") return reply(route, []);
    if (url.pathname === "/api/diagnostics") {
      const offset = Number(url.searchParams.get("offset"));
      const thread = url.searchParams.get("thread_id");
      listRequests.push({ account, thread, offset });
      assert.equal(url.searchParams.get("limit"), "20");
      assert.ok([`thread-${account}`, `empty-thread-${account}`].includes(thread),
        "客户端不得把其他账号的旧线程带入列表请求");
      if (failListOffset === offset) {
        failListOffset = null;
        return reply(route, { detail: "列表暂不可用" }, 503);
      }
      if (account === "b" || thread.startsWith("empty-thread"))
        return reply(route, { items: [], has_more: false, next_offset: null });
      // 第二页重复一个 ID，模拟加载更早记录期间列表发生变化。
      const items = offset ? [records[1], ...records.slice(20)] : records.slice(0, 20);
      return reply(route, { items, has_more: !offset, next_offset: offset ? null : 20 });
    }
    return reply(route, { items: [], total: 0, has_more: false, next_cursor: null });
  });
  await page.route("**/diagnostic/start", route => {
    const payload = route.request().postDataJSON();
    starts.push(payload);
    assert.equal(payload.thread_id, `thread-${account}`);
    const sessionId = `new-${account}-${starts.length}`;
    records.unshift(entry(sessionId));
    return reply(route, { session_id: sessionId });
  });
  await page.route("**/diagnostic/*/next", route => {
    const sessionId = new URL(route.request().url()).pathname.split("/")[2];
    assert.equal(route.request().method(), "POST");
    const selected = sessionId === "active-a" ? question
      : { ...question, id: `question-${sessionId}`, stem: `新诊断题 ${sessionId}` };
    nextRequests.push({ sessionId, questionId: selected.id });
    return reply(route, { status: "active", question: selected,
      progress: { answered: 2, max_questions: 6 } });
  });
  await page.route("**/diagnostic/*/report", route => {
    const sessionId = new URL(route.request().url()).pathname.split("/")[2];
    assert.equal(route.request().method(), "GET");
    reportRequests.push(sessionId);
    return reply(route, frozenReport);
  });

  async function enterThread(thread = "thread-a") {
    await page.locator("#thread-select").selectOption(thread);
    await page.locator("#tab-quiz").click();
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.waitForFunction(() => !diagnosticListLoading
      && document.querySelector("#diagnostic-list-status").textContent.length > 0);
  }
  async function openRecord(sessionId) {
    await page.locator("#diagnostic-list").selectOption(sessionId);
    await page.locator("#diagnostic-open").click();
  }
  async function optionIds() {
    return page.locator("#diagnostic-list option").evaluateAll(options =>
      options.map(option => option.value).filter(Boolean));
  }
  async function installLateResponseProbe() {
    // 将响应正文读完后延迟交给 app，确保测试 epoch 防护而非仅依赖网络 abort。
    await page.evaluate(() => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, options = {}) => {
        const response = await originalFetch(input, options);
        if (window.__holdPath !== new URL(input, location.href).pathname) return response;
        window.__holdPath = null;
        const buffered = new Response(await response.text(), {
          status: response.status, headers: response.headers,
        });
        window.__heldResponse = { aborted: !!options.signal?.aborted };
        options.signal?.addEventListener("abort", () => {
          window.__heldResponse.aborted = true;
        }, { once: true });
        await new Promise(resolve => { window.__releaseResponse = resolve; });
        return buffered;
      };
    });
  }
  async function holdRequest(pathname, action) {
    await page.evaluate(({ pathname, action }) => {
      window.__holdPath = pathname;
      window.__heldResponse = null;
      window.__pendingRequest = action === "list" ? loadDiagnosticList(true)
        : document.querySelector("#diagnostic-open").onclick();
    }, { pathname, action });
    await page.waitForFunction(() => !!window.__heldResponse);
  }
  async function releaseRequest() {
    assert.equal(await page.evaluate(() => window.__heldResponse.aborted), true,
      "切换模式、线程或账号必须中止旧请求");
    await page.evaluate(async () => {
      window.__releaseResponse();
      await window.__pendingRequest;
    });
  }

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await enterThread();
    assert.equal((await optionIds()).length, 20);
    assert.equal(await page.locator("#diagnostic-open").isDisabled(), true);
    assert.equal(starts.length, 0, "列表读取和模式切换不得自动开始诊断");
    await openRecord("active-a");
    await page.locator("#q-stem").filter({ hasText: question.stem }).waitFor();
    assert.deepEqual(await page.evaluate(() => [diagnosticSessionId, currentQuestion.id]),
      ["active-a", question.id]);

    await page.reload({ waitUntil: "networkidle" });
    assert.equal(await page.locator("#thread-select").inputValue(), "");
    assert.equal(await page.evaluate(() => diagnosticSessionId), null);
    await enterThread();
    assert.equal(nextRequests.length, 1, "刷新后的列表不能自动续题");
    await openRecord("active-a");
    await page.locator("#q-stem").filter({ hasText: question.stem }).waitFor();
    assert.deepEqual(nextRequests, [
      { sessionId: "active-a", questionId: question.id },
      { sessionId: "active-a", questionId: question.id },
    ], "明确恢复必须使用原 SID 和原待答题 ID");
    assert.equal(starts.length, 0);
    await openRecord("completed-a");
    await page.locator("#stats-summary").filter({ hasText: "冻结报告建议" }).waitFor();
    assert.deepEqual(await page.evaluate(() => lastDiagnosticReport), frozenReport);
    assert.deepEqual(reportRequests, ["completed-a"]);
    assert.equal(nextRequests.length, 2, "已完成记录应读取报告，不得重新选题");

    failListOffset = 20;
    await page.locator("#diagnostic-list-more").click();
    await page.locator("#diagnostic-list-retry").waitFor({ state: "visible" });
    assert.equal((await optionIds()).length, 20, "翻页失败保留已载入记录");
    await page.locator("#diagnostic-list-retry").click();
    await page.waitForFunction(() => diagnosticListItems.length === 22 && !diagnosticListLoading);
    assert.equal((await optionIds()).filter(id => id === "completed-a").length, 1);
    assert.equal(await page.locator("#diagnostic-list-more").isVisible(), false);
    assert.deepEqual(listRequests.slice(-2).map(request => request.offset), [20, 20]);
    await page.locator("#diagnostic-domains").selectOption(["domain-a"]);
    await page.locator("#diagnostic-start").click();
    await page.locator("#q-stem").filter({ hasText: "新诊断题 new-a-1" }).waitFor();
    await page.waitForFunction(() => diagnosticListItems.some(item => item.session_id === "new-a-1"));
    assert.equal(starts.length, 1, "显式开始新诊断建立独立 SID 并刷新列表");
    assert.notEqual(nextRequests.at(-1).sessionId, "active-a");
    assert.equal(await page.locator("#diagnostic-chat").isVisible(), false);

    await installLateResponseProbe();
    await holdRequest("/api/diagnostics", "list");
    await page.locator("#quiz-mode").selectOption("random");
    await releaseRequest();
    assert.deepEqual(await optionIds(), [], "退出诊断模式后迟到列表不能回填");
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.waitForFunction(() => diagnosticListItems.length === 20 && !diagnosticListLoading);
    await page.locator("#diagnostic-list").selectOption("completed-a");
    await holdRequest("/diagnostic/completed-a/report", "open");
    await page.locator("#quiz-mode").selectOption("random");
    await releaseRequest();
    assert.equal(await page.evaluate(() => lastDiagnosticReport), null,
      "退出诊断模式后迟到报告不能回填");

    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.waitForFunction(() => diagnosticListItems.length === 20 && !diagnosticListLoading);
    await page.locator("#diagnostic-list").selectOption("active-a");
    await holdRequest("/diagnostic/active-a/next", "open");
    await page.locator("#thread-select").selectOption("empty-thread-a");
    await releaseRequest();
    assert.equal(await page.evaluate(() => currentQuestion), null,
      "切换线程后迟到题目不能回填");
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.locator("#diagnostic-list-status").filter({ hasText: "本线程暂无诊断记录" }).waitFor();
    assert.deepEqual(await optionIds(), []);
    assert.equal(await page.locator("#diagnostic-open").isDisabled(), true);

    await enterThread();
    await holdRequest("/api/diagnostics", "list");
    account = "b";
    await page.evaluate(() => verifyIdentity(true));
    await enterThread("thread-b");
    await releaseRequest();
    assert.deepEqual(await optionIds(), [], "B 账号没有 A 的诊断列表或迟到记录");
    assert.equal(await page.evaluate(() => diagnosticSessionId), null);
    assert.equal(await page.locator("#q-options .option").count(), 0);
    assert.equal(await page.evaluate(() => Object.values(localStorage).some(value =>
      ["active-a", "completed-a", "pending-a-question", "new-a-1"].some(id => value.includes(id)))), false,
      "不得把诊断 SID 或题目载荷保存到 localStorage");
    assert.equal(starts.length, 1, "刷新、恢复和切账号均不得自动创建诊断");
    assert.deepEqual(pageErrors, []);
    console.log("diagnostic restore browser e2e passed: explicit restore, frozen reports, pagination, stale-response isolation");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
