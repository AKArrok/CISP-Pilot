"use strict";

const assert = require("node:assert/strict");
const playwrightModule = process.env.CISP_PLAYWRIGHT_MODULE || process.argv[2] || "playwright";
const { chromium } = require(playwrightModule);

const baseUrl = process.env.CISP_BASE_URL || process.argv[4] || "http://127.0.0.1:9528";

async function main() {
  const launchOptions = { headless: true };
  const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || process.argv[3];
  if (executablePath) launchOptions.executablePath = executablePath;

  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  const pageErrors = [];
  let domainRequests = 0;
  let statsRequests = 0;
  let readinessPayload = {
    status: "migration_required",
    schema: { expected_revision: "f1a2b3c4d5e6", current_revisions: [] },
    assets: { ready: true, error: null },
  };
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.route("**/api/ready", route => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify(readinessPayload),
  }));
  await page.route("**/api/domains", route => {
    domainRequests += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/stats?*", route => {
    statsRequests += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    const banner = page.locator("#service-status");
    await banner.waitFor({ state: "visible" });
    assert.match(await banner.innerText(), /数据库迁移尚未到 f1a2b3c4d5e6/);
    assert.equal(await page.locator("#chat-send").isDisabled(), true);
    assert.equal(await page.locator("#quiz-next").isDisabled(), true);
    assert.equal(domainRequests, 0, "未就绪时不得加载知识域");
    assert.equal(statsRequests, 0, "未就绪时不得加载画像");

    readinessPayload = {
      status: "assets_invalid",
      schema: {
        expected_revision: "f1a2b3c4d5e6",
        current_revisions: ["f1a2b3c4d5e6"],
      },
      assets: { ready: false, error: "manifest mismatch" },
    };
    await page.reload({ waitUntil: "networkidle" });
    await banner.waitFor({ state: "visible" });
    assert.match(await banner.innerText(), /题库或索引与清单不一致/);
    assert.equal(await page.locator("#chat-send").isDisabled(), true);
    assert.equal(await page.locator("#quiz-next").isDisabled(), true);
    assert.equal(domainRequests, 0, "资产失效时不得加载知识域");
    assert.equal(statsRequests, 0, "资产失效时不得加载画像");
    assert.deepEqual(pageErrors, []);
    console.log("readiness browser e2e passed: migration and asset failures disable application");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
