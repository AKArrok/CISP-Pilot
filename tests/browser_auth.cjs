"use strict";

const assert = require("node:assert/strict");

async function registerAndCreateThread(page) {
  const username = `e2e${process.pid}${Date.now()}${Math.floor(Math.random() * 10000)}`;
  await page.locator("#auth-username").fill(username);
  await page.locator("#auth-password").fill(`BrowserTest!${username}`);
  await page.locator("#auth-register").click();
  await page.locator("#thread-create").waitFor({ state: "visible", timeout: 30_000 });
  await page.locator("#auth-status").filter({ hasText: "登录成功" }).waitFor({ timeout: 30_000 });
  await page.locator("#thread-create").click();
  await page.waitForFunction(() => !!document.querySelector("#thread-select")?.value);
  await page.waitForFunction(() => !chatHistoryLoading);
  const threadId = await page.locator("#thread-select").inputValue();
  assert.match(threadId, /^[0-9a-f-]{36}$/i);
  assert.equal(await page.locator("#chat-send").isDisabled(), false);
  return threadId;
}

module.exports = { registerAndCreateThread };
