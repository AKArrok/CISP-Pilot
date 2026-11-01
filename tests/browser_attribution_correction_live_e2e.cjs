"use strict";

const assert = require("node:assert/strict");
const { registerAndCreateThread } = require("./browser_auth.cjs");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);

const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:9530";

async function main() {
  const launchOptions = { headless: true };
  if (executablePath) launchOptions.executablePath = executablePath;
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const failedResponses = [];
  const reviewResponses = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error" && !message.text().includes("401 (Unauthorized)"))
      consoleErrors.push(message.text());
  });
  page.on("response", response => {
    if (response.url().includes("/api/attribution-review")) {
      reviewResponses.push(response.json().catch(() => null));
    }
    if (response.status() >= 400 && !response.url().endsWith("/api/auth/me")) {
      failedResponses.push(response.status() + " " + response.url());
    }
  });

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await registerAndCreateThread(page);
    await page.locator("#tab-quiz").click();
    await page.getByText(/已加载 30 \/ \d+ 道题/).waitFor({ timeout: 10_000 });
    const firstReview = await reviewResponses.at(-1);
    assert.ok(firstReview.items[0].question_stem, "审阅页需要题干供用户判断");
    assert.ok(Object.keys(firstReview.items[0].question_options).length,
      "审阅页需要选项供用户判断");
    assert.ok(firstReview.items[0].candidate_point_name, "候选知识点需展示中文名");
    assert.equal("answer" in firstReview.items[0], false, "审阅接口不应返回正确答案");
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 30);
    const firstPageIds = await page.locator("#attribution-table tbody tr").evaluateAll(
      rows => rows.map(row => row.querySelector(".attribution-question-id").textContent.trim()),
    );
    assert.equal(new Set(firstPageIds).size, 30, "首批题目 ID 不应重复");
    await page.getByRole("button", { name: /加载更多题目/ }).click();
    await page.getByText(/已加载 60 \/ \d+ 道题/).waitFor({ timeout: 10_000 });
    assert.equal(await page.locator("#attribution-table tbody tr").count(), 60);
    const loadedIds = await page.locator("#attribution-table tbody tr").evaluateAll(
      rows => rows.map(row => row.querySelector(".attribution-question-id").textContent.trim()),
    );
    assert.equal(new Set(loadedIds).size, 60, "连续页之间不应重复题目");
    const firstRow = page.locator("#attribution-table tbody tr").first();
    await firstRow.waitFor({ state: "visible", timeout: 10_000 });
    const questionId = (await firstRow.locator(".attribution-question-id").innerText()).trim();
    const currentPoint = firstReview.items[0].candidate_point_id;
    const questionDetails = firstRow.locator(".attribution-question-details");
    await questionDetails.locator("summary").click();
    const detailsText = await questionDetails.innerText();
    assert.ok(detailsText.includes(firstReview.items[0].question_stem));
    assert.ok(detailsText.includes(Object.values(firstReview.items[0].question_options)[0]));
    await page.locator("#attribution-review-query").fill(questionId);
    await page.getByRole("button", { name: "筛选", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelector("#attribution-profile")?.getAttribute("aria-busy") === "false",
    { timeout: 10_000 });
    const searchResult = await reviewResponses.at(-1);
    assert.ok(searchResult.total >= 1, "题目 ID 子串应至少匹配一道题");
    assert.ok(searchResult.items.some(item => item.question_id === questionId),
      "筛选结果必须包含被选中的完整题目 ID");
    const exactRowIndex = await page.locator("#attribution-table tbody tr").evaluateAll(
      (rows, id) => rows.findIndex(row =>
        row.querySelector(".attribution-question-id").textContent.trim() === id),
      questionId,
    );
    assert.notEqual(exactRowIndex, -1, "筛选表格必须展示该完整题目 ID");
    const searchedRow = page.locator("#attribution-table tbody tr").nth(exactRowIndex);

    await searchedRow.getByRole("button", { name: "纠正", exact: true }).click();
    const select = searchedRow.locator(".attribution-correction select");
    try {
      await select.locator("option").first().waitFor({
        state: "attached", timeout: 10_000,
      });
    } catch (error) {
      const actionText = await searchedRow.locator("td").last().innerText();
      throw new Error(
        "纠错知识点未加载: action=" + actionText
        + "; http=" + failedResponses.join(" | ")
        + "; " + error.message,
      );
    }
    const values = await select.locator("option").evaluateAll(
      options => options.map(option => option.value),
    );
    assert.ok(values.length >= 1, "真实知识点目录至少应有一个 active 选项");
    const targetPoint = values.find(value => value !== currentPoint) || values[0];
    const pointSearch = page.getByRole("searchbox", { name: "搜索目标知识点" });
    await pointSearch.fill(targetPoint);
    assert.equal(await select.locator("option").count(), 1,
      "真实知识点应可用精确 ID 定位");
    if (targetPoint !== currentPoint) {
      assert.equal(await select.inputValue(), "",
        "筛选后不得静默选择不同知识点");
    }
    await select.selectOption(targetPoint);
    const targetName = (await select.locator("option:checked").innerText())
      .split(" / ").slice(1).join(" / ");
    await page.getByRole("textbox", { name: "纠错依据" }).fill("真实服务浏览器 E2E");
    await searchedRow.getByRole("button", { name: "保存纠错", exact: true }).click();

    await page.locator("#stats-summary").filter({ hasText: "纠错已保存" })
      .waitFor({ timeout: 10_000 });
    const correctedRow = page.locator("#attribution-table tbody tr").nth(exactRowIndex);
    await correctedRow.getByRole("button", { name: "再次纠正", exact: true })
      .waitFor({ timeout: 10_000 });
    const rowText = await correctedRow.innerText();
    assert.match(rowText, /用户纠错/);
    assert.ok(rowText.includes(targetPoint));
    assert.equal(await correctedRow.locator(".attribution-point-name").innerText(), targetName);

    assert.deepEqual(pageErrors, [], "页面脚本错误: " + pageErrors.join(" | "));
    assert.deepEqual(consoleErrors, [], "浏览器控制台错误: " + consoleErrors.join(" | "));
    assert.deepEqual(failedResponses, [], "HTTP 错误: " + failedResponses.join(" | "));
    console.log(
      "attribution correction live e2e passed: question=" + questionId
      + ", search_matches=" + searchResult.total
      + ", from=" + currentPoint + ", to=" + targetPoint,
    );
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
