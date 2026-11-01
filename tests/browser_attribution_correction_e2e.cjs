"use strict";

const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const { registerAndCreateThread } = require("./browser_auth.cjs");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);

const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:8765";
const state = {
  corrected: false,
  dedupReturned: false,
  post: null,
  reviewRequests: [],
  observabilityInvalid: false,
  ledgerAnomaly: false,
};

function statsPayload() {
  const item = {
    question_id: "q-ui",
    candidate_point_id: state.corrected ? "kp-b" : "kp-a",
    candidate_domain: state.corrected ? "domain-b" : "domain-a",
    attribution_status: state.corrected ? "confirmed" : "probable",
    status: state.corrected ? "confirmed" : "probable",
    evidence_count: 2,
    reason: state.corrected ? "用户确认考点" : "系统候选待确认",
  };
  if (state.corrected) item.source = "user_correction";
  return {
    total_attempts: 1,
    total_accuracy: 0,
    legacy_applied_attempts_without_point: 2,
    domains: [],
    knowledge_points: [],
    attributions: [item],
    attribution_status_counts: { confirmed: 0, probable: 1, unverified: 0 },
    attribution_evidence_channels: {
      system_candidates: { confirmed: 0, probable: 1, unverified: 0 },
      user_corrections: {
        events: Number(state.corrected),
        profile_replays: Number(state.corrected),
        calibration_replacements: 0,
        calibrations_retracted: 0,
      },
      calibration_outcomes: { pending: 0, confirmed: 0, retracted: 0, inconclusive: 1 },
    },
    pending_calibrations: [],
    weak_domains: [],
    review_due_domains: [],
    ask_unverified_domains: [],
    review_due_cards: 0,
  };
}

function observabilityPayload() {
  if (state.observabilityInvalid) return { events: Number(state.corrected) };
  return {
    metric_semantics: "operational_feedback_not_accuracy",
    not_accuracy_metric: true,
    window: { since: null, latest_event_id: state.corrected ? 1 : null },
    events: Number(state.corrected),
    unique_questions: Number(state.corrected),
    repeat_events: 0,
    reason_provided_events: Number(state.corrected),
    profile_replays: Number(state.corrected),
    calibration_replacements: 0,
    calibrations_retracted: 0,
    by_target_domain: state.corrected ? { "domain-b": 1 } : {},
    by_previous_status: state.corrected ? { probable: 1 } : {},
    ledger_integrity: {
      snapshot_scope: "current_thread_not_event_window",
      confirmed_calibrations: 0,
      confirmed_missing_applied_evidence: 0,
      confirmed_duplicate_applied_evidence: 0,
      confirmed_unexpected_applied_evidence: 0,
      confirmed_mismatched_applied_attempts: Number(state.ledgerAnomaly),
      nonconfirmed_applied_attempts: 0,
      orphan_applied_attempts: 0,
      anomalies_total: Number(state.ledgerAnomaly),
      status: state.ledgerAnomaly ? "attention" : "ok",
    },
  };
}

function reviewItem(questionId, pointId, domain, status, reason) {
  const corrected = state.corrected && questionId === "q-ui";
  return {
    question_id: questionId,
    question_stem: "以下哪项体现了信息安全保障？",
    question_options: { A: "选项甲", B: "选项乙" },
    candidate_point_id: corrected ? "kp-b" : pointId,
    candidate_point_name: corrected ? "知识点 B" : "知识点 A",
    candidate_domain: corrected ? "domain-b" : domain,
    attribution_status: corrected ? "confirmed" : status,
    status: corrected ? "confirmed" : status,
    evidence_count: 2,
    legacy_applied_attempts_without_point: questionId === "q-next" ? 2 : 0,
    reason: corrected ? "用户确认考点" : reason,
    ...(corrected ? { source: "user_correction" } : {}),
  };
}


async function main() {
  const launchOptions = { headless: true };
  const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
  if (executablePath) launchOptions.executablePath = executablePath;
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const consoleErrors = [];
  const pageErrors = [];
  const unexpectedUnauthorized = [];
  page.on("response", response => {
    if (response.status() === 401 && !response.url().endsWith("/api/auth/me"))
      unexpectedUnauthorized.push(response.url());
  });
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
  await page.route("**/api/ready", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ready" }),
  }));  await page.route("**/api/domains", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: "[]",
  }));
  await page.route("**/api/stats?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(statsPayload()),
  }));
  await page.route("**/api/attribution-review?*", route => {
    const url = new URL(route.request().url());
    const query = url.searchParams.get("question_id") || "";
    const cursor = url.searchParams.get("after_question_id");
    state.reviewRequests.push({ query, cursor });
    let payload;
    if (query) {
      payload = {
        items: query === "q-ui"
          ? [reviewItem("q-ui", "kp-a", "domain-a", "probable", "系统候选待确认")]
          : [],
        total: query === "q-ui" ? 1 : 0,
        has_more: false,
        next_cursor: null,
      };
    } else if (cursor) {
      payload = {
        items: [reviewItem("q-next", "kp-a", "domain-a", "confirmed", "后续候选")],
        total: 31,
        has_more: false,
        next_cursor: null,
      };
    } else {
      payload = {
        items: [reviewItem("q-ui", "kp-a", "domain-a", "probable", "系统候选待确认")],
        total: 31,
        has_more: true,
        next_cursor: "q-ui",
      };
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
  await page.route("**/api/attribution-observability?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(observabilityPayload()),
  }));
  await page.route("**/api/knowledge-points?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([
      { id: "kp-a", domain: "domain-a", name: "知识点 A", status: "active" },
      { id: "kp-b", domain: "domain-b", name: "知识点 B", status: "active" },
    ]),
  }));
  await page.route("**/api/attributions/*/correction", async route => {
    const payload = route.request().postDataJSON();
    assert.ok(route.request().headers()["x-csrf-token"], "纠错请求必须附带 CSRF token");
    if (state.corrected && !state.dedupReturned) {
      state.dedupReturned = true;
      state.dedupPost = payload;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          question_id: "q-ui",
          corrected_point_id: "kp-b",
          corrected_domain: "domain-b",
          deduplicated: true,
          replayed_attempts: 0,
          retracted_calibrations: 0,
          replacement_calibration_id: null,
        }),
      });
      return;
    }
    if (state.corrected) {
      state.failurePost = payload;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "旧证据缺少精确账本" }),
      });
      return;
    }
    state.post = payload;
    state.corrected = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        question_id: "q-ui",
        corrected_point_id: "kp-b",
        replayed_attempts: 1,
        retracted_calibrations: 0,
        replacement_calibration_id: null,
      }),
    });
  });

  try {
    await page.goto(baseUrl + "/", { waitUntil: "networkidle" });
    const createdThreadId = await registerAndCreateThread(page);
    await page.locator("#tab-quiz").click();
    await page.getByText("当前线程尚无纠错反馈。").waitFor();
    await page.getByText(/校准账本完整性正常/).waitFor();
    await page.getByText("已加载 1 / 31 道题").waitFor();
    await page.getByRole("button", { name: /加载更多题目/ }).click();
    await page.getByText("已加载 2 / 31 道题").waitFor();
    assert.deepEqual(
      await page.locator("#attribution-table tbody tr").evaluateAll(
        rows => rows.map(row => row.querySelector(".attribution-question-id").textContent),
      ),
      ["q-ui", "q-next"],
    );
    const legacyRow = page.locator("#attribution-table tbody tr").nth(1);
    assert.equal(await legacyRow.locator(".correction-open").isDisabled(), true);
    assert.match(await legacyRow.locator(".attribution-legacy-note").innerText(),
      /历史已入画像作答 2 条缺精确归因/);
    assert.match(await page.locator("#stats-summary").innerText(),
      /历史已入画像作答 2 条缺精确归因/);
    await page.locator("#attribution-review-query").fill("q-ui");
    await page.getByRole("button", { name: "筛选", exact: true }).click();
    await page.getByText("已加载 1 / 1 道题").waitFor();
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 1);
    const reviewRow = page.locator("#attribution-table tbody tr").first();
    assert.match(await reviewRow.locator(".attribution-point-name").innerText(), /知识点 A/);
    await reviewRow.locator(".attribution-question-details summary").click();
    assert.match(await reviewRow.locator(".attribution-question-details").innerText(), /选项甲/);
    assert.match(await page.locator("#attribution-observability").innerText(), /不是准确率/);
    assert.match(await page.locator("#stats-summary").innerText(), /校准未形成结论 1 个/);
    await page.getByRole("button", { name: "纠正", exact: true }).click();
    const select = page.locator(".attribution-correction select");
    await select.locator("option").first().waitFor({ state: "attached" });
    assert.equal(await select.locator("option").count(), 2);
    const pointSearch = page.getByRole("searchbox", { name: "搜索目标知识点" });
    await pointSearch.fill("不存在的知识点");
    assert.equal(await select.locator("option").count(), 0);
    assert.match(await page.locator(".correction-match-count").innerText(), /匹配 0 \/ 2/);
    await page.getByRole("button", { name: "保存纠错", exact: true }).click();
    await page.getByText("请先选择目标知识点").waitFor();
    assert.equal(state.post, null, "无匹配选项不应提交纠错");
    await pointSearch.fill("知识点 B");
    assert.equal(await select.locator("option").count(), 1);
    assert.equal(await select.inputValue(), "", "筛选后不得静默选择首个匹配项");
    await select.selectOption("kp-b");
    await page.getByRole("textbox", { name: "纠错依据" }).fill("用户确认考点");
    await page.getByRole("button", { name: "保存纠错", exact: true }).click();

    await page.getByText("纠错已保存，并重放 1 条画像证据").waitFor();
    await page.getByRole("button", { name: "再次纠正", exact: true }).waitFor();
    const rowText = await page.locator("#attribution-table tbody tr").innerText();
    assert.match(rowText, /kp-b/);
    assert.match(rowText, /知识点 B/);
    assert.match(rowText, /用户纠错/);
    assert.equal(state.post.corrected_point_id, "kp-b");
    assert.equal(state.post.reason, "用户确认考点");
    assert.equal(state.post.thread_id, createdThreadId);
    await page.getByText(/反馈 1 次，涉及 1 道题/).waitFor();
    const observationText = await page.locator("#attribution-observability").innerText();
    assert.match(observationText, /画像重放 1 次/);
    assert.match(observationText, /domain-b 1 次/);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await page.evaluate(() => {
        const panel = document.querySelector("#attribution-observability");
        const badge = panel.querySelector(".feedback-observability-title span");
        const panelRect = panel.getBoundingClientRect();
        const badgeRect = badge.getBoundingClientRect();
        return {
          viewportWidth: window.innerWidth,
          pageWidth: document.documentElement.scrollWidth,
          panelLeft: panelRect.left,
          panelRight: panelRect.right,
          badgeRight: badgeRect.right,
        };
      });
      assert.ok(layout.panelLeft >= 0, `反馈卡左侧越界: ${JSON.stringify(layout)}`);
      assert.ok(layout.panelRight <= layout.viewportWidth + 1,
        `反馈卡右侧越界: ${JSON.stringify(layout)}`);
      assert.ok(layout.badgeRight <= layout.panelRight + 1,
        `非准确率标识溢出: ${JSON.stringify(layout)}`);
      assert.ok(layout.pageWidth <= layout.viewportWidth + 1,
        `页面产生横向溢出: ${JSON.stringify(layout)}`);
    }
    await page.setViewportSize({ width: 1280, height: 900 });

    state.ledgerAnomaly = true;
    await page.evaluate(() => loadStats());
    await page.getByText(/校准账本发现 1 项结构异常/).waitFor();
    await page.getByText(/账本异常：缺失认领 0；重复认领 0；意外认领 0；精确证据错配 1/).waitFor();
    assert.equal(
      await page.locator("#attribution-observability").getAttribute("data-state"),
      "attention",
    );

    state.observabilityInvalid = true;
    await page.evaluate(() => loadStats());
    await page.getByText("纠错反馈暂不可用，不影响答题画像。").waitFor();
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 1);
    assert.ok(state.reviewRequests.some(request => request.query === "q-ui"));
    assert.ok(state.reviewRequests.some(request => request.cursor === "q-ui"));

    await page.getByRole("button", { name: "再次纠正", exact: true }).click();
    await page.locator(".attribution-correction select").selectOption("kp-b");
    await page.getByRole("button", { name: "保存纠错", exact: true }).click();
    await page.getByText("归因未变化，未新增纠错事件").waitFor();
    assert.equal(state.dedupPost.corrected_point_id, "kp-b");

    await page.getByRole("button", { name: "再次纠正", exact: true }).click();
    await page.locator(".attribution-correction select").selectOption("kp-b");
    await page.getByRole("button", { name: "保存纠错", exact: true }).click();
    await page.getByText("纠错失败：旧证据缺少精确账本").waitFor();
    assert.equal(state.failurePost.corrected_point_id, "kp-b");
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(unexpectedUnauthorized, [], "业务请求不应出现未预期的 401");
    assert.ok(
      consoleErrors.some(message => message.includes("409 (Conflict)")),
      "预期失败请求应保留 409 控制台证据",
    );
    assert.deepEqual(
      consoleErrors.filter(message => !message.includes("409 (Conflict)")
        && !message.includes("401 (Unauthorized)")),
      [],
    );

    const screenshot = path.join(os.tmpdir(), "cisp-attribution-correction.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await page.locator("#auth-logout").click();
    await page.locator("#auth-register").waitFor({ state: "visible" });
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 0);
    assert.equal(await page.locator("#stats-summary").innerText(), "");
    const secondThreadId = await registerAndCreateThread(page);
    assert.notEqual(secondThreadId, createdThreadId);
    assert.equal(await page.locator(`#thread-select option[value="${createdThreadId}"]`).count(), 0,
      "新账号线程列表不得包含上一账号线程");
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 0,
      "切换账号后不得保留上一账号的纠错列表");
    console.log(JSON.stringify({ status: "passed", row: rowText, screenshot }));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
