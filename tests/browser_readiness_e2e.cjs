"use strict";

const assert = require("node:assert/strict");
const { registerAndCreateThread } = require("./browser_auth.cjs");
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
  let ready = false;
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.route("**/api/ready", route => route.fulfill({
    status: ready ? 200 : 503,
    contentType: "application/json",
    body: JSON.stringify({ status: ready ? "ready" : "unavailable" }),
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
    assert.match(await banner.innerText(), /系统尚未就绪/);
    assert.equal(await page.locator("#chat-send").isDisabled(), true);
    assert.equal(await page.locator("#quiz-next").isDisabled(), true);
    assert.equal(domainRequests, 0, "未就绪时不得加载知识域");
    assert.equal(statsRequests, 0, "未就绪时不得加载画像");

    ready = true;
    await page.locator("#service-retry").click();
    await banner.waitFor({ state: "hidden" });
    await page.locator("#auth-register").waitFor({ state: "visible" });
    assert.equal(await page.locator("#auth-register").isDisabled(), false);
    assert.equal(await page.locator("#chat-send").isDisabled(), true);
    assert.equal(await page.locator("#quiz-next").isDisabled(), true);
    assert.equal(domainRequests, 0, "未登录时不得加载知识域");
    assert.equal(statsRequests, 0, "未登录时不得加载画像");
    await registerAndCreateThread(page);
    await page.waitForFunction(() => document.querySelector("#chat-send")?.disabled === false);
    assert.ok(domainRequests >= 1, "选择本人线程后应加载知识域");
    assert.deepEqual(pageErrors, []);
    console.log("readiness browser e2e passed: unavailable disables, ready requires login");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
