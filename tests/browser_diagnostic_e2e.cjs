"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);

const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:9528";

async function main() {
  const launchOptions = { headless: true };
  const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
  if (executablePath) {
    launchOptions.executablePath = executablePath;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const failedResponses = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", response => {
    if (response.status() >= 400) {
      failedResponses.push(`${response.status()} ${response.url()}`);
    }
  });

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator("#tab-quiz").click();
    await page.locator("#quiz-mode").selectOption("diagnostic");
    await page.locator("#diagnostic-form").waitFor({ state: "visible" });
    await page.locator("#diagnostic-domains option").first().waitFor();
    const domain = await page.locator("#diagnostic-domains option").first().getAttribute("value");
    assert.ok(domain, "知识域下拉框应由 /api/domains 填充");

    await page.locator("#diagnostic-domains").selectOption([domain]);
    await page.locator("#diagnostic-goal").fill("浏览器 E2E 验证");
    await page.locator("#diagnostic-experience").fill("用于验证诊断交互，不作为能力结论");
    await page.locator("#diagnostic-start").click();

    for (let answered = 0; answered < 6; answered += 1) {
      const option = page.locator("#q-options .option:not(.locked)").first();
      await option.waitFor({ state: "visible", timeout: 10_000 });
      await page.locator("#diagnostic-reason").fill(`E2E 第 ${answered + 1} 题`);
      await option.click();
    }

    await page.locator("#stats-summary").filter({ hasText: "诊断完成" })
      .waitFor({ timeout: 10_000 });
    await page.locator("#diagnostic-chat").waitFor({ state: "visible" });
    assert.ok(await page.locator("#stats-table tbody tr").count() >= 1,
      "诊断报告应至少包含一个知识域结果");

    await page.locator("#quiz-mode").selectOption("random");
    assert.equal(await page.evaluate(() => diagnosticMode), false,
      "切换到随机练习后必须退出诊断状态");
    await page.locator("#quiz-next").click();
    const regularOption = page.locator("#q-options .option:not(.locked)").first();
    await regularOption.waitFor({ state: "visible", timeout: 10_000 });
    await regularOption.click();
    try {
      await page.locator("#q-feedback").waitFor({ state: "visible", timeout: 10_000 });
      await page.locator("#q-verdict").filter({ hasText: /回答正确|回答错误/ })
        .waitFor({ state: "visible", timeout: 10_000 });
    } catch (error) {
      const state = await page.evaluate(() => ({
        diagnosticMode,
        questionClass: document.querySelector("#quiz-question")?.className,
        feedbackClass: document.querySelector("#q-feedback")?.className,
        verdictClass: document.querySelector("#q-verdict")?.className,
        verdictText: document.querySelector("#q-verdict")?.textContent,
      }));
      throw new Error(`普通答题反馈未显示: ${JSON.stringify(state)}; ${error.message}`);
    }
    const confirmedRows = await page.evaluate(async () => {
      const response = await fetch("/api/attributions?status=confirmed&limit=1");
      return response.json();
    });
    assert.equal(confirmedRows.length, 1,
      "隔离归因目录必须提供一条 confirmed 题用于画像验证");
    const confirmedStatus = await page.evaluate(async questionId => {
      const response = await fetch("/quiz/answer", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          question_id: questionId,
          choice: "A",
          thread_id: window.localStorage.getItem("cisp-thread-id"),
        }),
      });
      return response.status;
    }, confirmedRows[0].question_id);
    assert.equal(confirmedStatus, 200, "confirmed 题应能写入线程画像");

    await page.evaluate(() => loadStats());
    await page.locator("#stats-summary").filter({ hasText: "累计答题" })
      .waitFor({ timeout: 10_000 });
    assert.ok(await page.locator("#knowledge-point-table tbody tr").count() >= 1,
      "普通刷题后应显示当前线程的知识点画像");

    const attributions = await page.evaluate(async () => {
      const [probableResponse, confirmedResponse] = await Promise.all([
        fetch("/api/attributions?status=probable&limit=500"),
        fetch("/api/attributions?status=confirmed&limit=500"),
      ]);
      return {
        probableStatus: probableResponse.status,
        confirmedStatus: confirmedResponse.status,
        probableRows: await probableResponse.json(),
        confirmedRows: await confirmedResponse.json(),
      };
    });
    assert.equal(attributions.probableStatus, 200);
    assert.equal(attributions.confirmedStatus, 200);
    const { probableRows, confirmedRows: confirmedRowsForCalibration } = attributions;
    const confirmedPoints = new Set(
      confirmedRowsForCalibration.map(row => row.candidate_point_id).filter(Boolean),
    );
    const questions = JSON.parse(fs.readFileSync("data/questions.json", "utf8"));
    const questionsById = new Map(questions.map(question => [question.id, question]));
    const calibrationSeed = probableRows
      .filter(row => confirmedPoints.has(row.candidate_point_id))
      .map(row => ({ row, question: questionsById.get(row.question_id) }))
      .find(({ question }) => {
        if (!question || question.needs_review || question.dup_of || !question.answer) return false;
        const answer = Array.isArray(question.answer) ? question.answer.join("") : String(question.answer);
        return Object.keys(question.options || {}).some(
          choice => /^[A-D]$/.test(choice) && !answer.includes(choice),
        );
      });
    assert.ok(calibrationSeed, "真实题库应有 probable 触发题及同考点 confirmed 校准题");
    const answer = Array.isArray(calibrationSeed.question.answer)
      ? calibrationSeed.question.answer.join("")
      : String(calibrationSeed.question.answer);
    const wrongChoice = Object.keys(calibrationSeed.question.options)
      .find(choice => /^[A-D]$/.test(choice) && !answer.includes(choice));
    const calibrationTrigger = await page.evaluate(async ({ questionId, choice, threadId }) => {
      const response = await fetch("/quiz/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: questionId, choice, thread_id: threadId }),
      });
      return { status: response.status, payload: await response.json() };
    }, {
      questionId: calibrationSeed.row.question_id,
      choice: wrongChoice,
      threadId: await page.evaluate(() => window.localStorage.getItem("cisp-thread-id")),
    });
    assert.equal(calibrationTrigger.status, 200);
    assert.equal(calibrationTrigger.payload.correct, false);
    assert.equal(calibrationTrigger.payload.attribution_status, "probable");
    await page.locator("#quiz-next").click();
    const calibrationNote = page.locator("#q-calibration");
    await calibrationNote.waitFor({ state: "visible", timeout: 10_000 });
    assert.match(await calibrationNote.innerText(), /校准题：/);
    assert.match(await calibrationNote.innerText(), /已完成 0\/2 条一致证据/);

    assert.deepEqual(pageErrors, [], `页面脚本错误: ${pageErrors.join(" | ")}`);
    assert.deepEqual(consoleErrors, [], `浏览器控制台错误: ${consoleErrors.join(" | ")}`);
    assert.deepEqual(failedResponses, [], `HTTP 错误: ${failedResponses.join(" | ")}`);
    console.log(`profile browser e2e passed: domain=${domain}, diagnostic=6, regular=1, calibration=1`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
