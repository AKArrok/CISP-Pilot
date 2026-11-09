/* Real browser checks against an explicitly isolated evaluation demo. No model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.CISP_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const origin = process.env.CISP_EVAL_TEST_ORIGIN;
  const password = process.env.CISP_EVAL_DEMO_PASSWORD;
  const output = process.env.CISP_EVAL_TEST_OUTPUT;
  const bundles = process.argv.slice(2);
  if (!origin || !password || !output || bundles.length !== 2) throw new Error('Require isolated origin, demo password, output directory and two bundle paths');
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw new Error('Loopback test target required');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? {executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page = await context.newPage();
  const errors = [], checks = [];
  let workspaceChecks = null;
  page.on('pageerror', error=>errors.push(error.message));
  try {
    await page.goto(origin+'/evaluation.html'); await page.waitForLoadState('networkidle');
    await page.locator('#login-name').fill('eval-reviewer'); await page.locator('#login-password').fill(password);
    await page.locator('#login button').click(); await page.locator('#workspace').waitFor({state:'visible'}); checks.push('real Cookie login');
    await page.locator('[data-tab="runs"]').click();
    const inputs = bundles.map(filename=>JSON.parse(fs.readFileSync(filename,'utf8')));
    for (let i=0;i<bundles.length;i++) {
      await page.locator('#import-file').setInputFiles(bundles[i]);
      await page.waitForFunction(name=>document.querySelector('#run-title').textContent===name, inputs[i].name);
    }
    let runs = await (await context.request.get(origin+'/api/evaluation/runs')).json();
    const left = runs.items.find(r=>r.name===inputs[0].name), right = runs.items.find(r=>r.name===inputs[1].name);
    assert.ok(left && right); assert.equal(left.planned,40);assert.equal(right.planned,40);checks.push('two real historical imports / 40 cases each');
    await page.locator('#import-file').setInputFiles(bundles[0]);
    await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('未重复新增'));
    const repeated=await (await context.request.get(origin+'/api/evaluation/runs')).json(); assert.equal(repeated.items.length,runs.items.length);checks.push('idempotent browser import');
    await page.locator('#status-filter').selectOption('failed');
    await page.locator('#case-list button').filter({hasText:'scenario-03'}).click();
    await page.locator('#case-detail').waitFor({state:'visible'});
    assert.ok((await page.locator('#answer').innerText()).length>100);
    assert.ok((await page.locator('#original-review').innerText()).includes('answer_sha256'));checks.push('real historical failure and bound evidence');
    await page.locator('#review-kind').selectOption('ai_assisted');
    await page.locator('#review-reason').fill('Browser workflow verification only; not semantic adjudication or independent human gold.');
    await page.locator('#review-form button').click();
    await page.waitForFunction(()=>document.querySelector('#review-list').textContent.includes('Browser workflow verification only'));
    const beforeStatus=await page.locator('#case-verdict').innerText();assert.equal(beforeStatus,'失败'); checks.push('append-only AI-assisted review; original failure retained');
    await page.screenshot({path:path.join(output,'failure-evidence.png'),fullPage:true});
    await page.locator('#status-filter').selectOption('all');
    await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#page-label').textContent.startsWith('21'));
    await page.locator('#search').fill('not-a-real-question');await page.locator('#search-button').click();
    await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='0–0 / 0');checks.push('pagination and empty search');
    await page.locator('#check-run').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('模型调用 0'));
    const csrf=(await (await context.request.get(origin+'/api/auth/me')).json()).csrf_token;
    const checkUrl=origin+`/api/evaluation/runs/${left.id}/checks`;
    const options={headers:{Origin:origin,'X-CSRF-Token':csrf}};
    const first=await (await context.request.post(checkUrl,options)).json(), second=await (await context.request.post(checkUrl,options)).json();assert.deepEqual(first,second);checks.push('offline check idempotency / zero model calls');
    const downloadPromise=page.waitForEvent('download');await page.locator('#export-json').click();const download=await downloadPromise;const downloadPath=path.join(output,'report.json');await download.saveAs(downloadPath);
    const exported=JSON.parse(fs.readFileSync(downloadPath,'utf8'));assert.equal(exported.run.planned,40);assert.ok(!JSON.stringify(exported).includes(inputs[0].cases[0].question));checks.push('real redacted JSON download');
    const mdPromise=page.waitForEvent('download');await page.locator('#export-md').click();const md=await mdPromise;await md.saveAs(path.join(output,'report.md'));checks.push('Markdown download');
    await page.locator('[data-tab="compare"]').click();await page.locator('#compare-left').selectOption(left.id);await page.locator('#compare-right').selectOption(right.id);
    await page.locator('#compare-form button').click();await page.waitForFunction(()=>document.querySelector('#comparison tbody tr'));
    const comparison=await (await context.request.get(origin+`/api/evaluation/compare?left=${left.id}&right=${right.id}`)).json();assert.equal(comparison.items.length,40);
    fs.writeFileSync(path.join(output,'comparison.json'),JSON.stringify(comparison,null,2));
    await page.screenshot({path:path.join(output,'version-comparison.png'),fullPage:true});checks.push('40-case version comparison');
    await page.locator('[data-tab="datasets"]').click();assert.ok((await page.locator('#dataset-list').innerText()).includes('development'));checks.push('frozen dataset view');
    await page.locator('[data-tab="overview"]').click();await page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'overview-mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.push('mobile layout without page overflow');
    await page.reload();await page.waitForLoadState('networkidle');await page.locator('#workspace').waitFor({state:'visible'});
    const detail=await (await context.request.get(origin+`/api/evaluation/runs/${left.id}/cases/scenario-03`)).json();assert.ok(detail.reviews.length>=1);checks.push('persistent review after reload');
    if(await page.locator('#jobs-nav').isVisible())workspaceChecks=await require('./evaluation_workspace_browser.cjs')({page,context,origin,output,options,checks,left,right});
    // Exercise untrusted imported text through the actual DOM, never innerHTML.
    const evil=JSON.parse(JSON.stringify(inputs[0]));evil.name='Synthetic XSS contract fixture';evil.role='synthetic';evil.visibility='synthetic';evil.cases=[{id:'xss-fixture',question:'<img src=x onerror="window.evaluationXss=true">',contract:{synthetic:true}}];evil.results=[];
    assert.equal((await context.request.post(origin+'/api/evaluation/imports',{...options,data:evil})).status(),200);
    await page.reload();await page.waitForLoadState('networkidle');await page.locator('[data-tab="runs"]').click();await page.locator('#run-list button').filter({hasText:evil.name}).click();
    await page.locator('#case-list button').filter({hasText:'xss-fixture'}).click();await page.locator('#case-detail').waitFor({state:'visible'});
    assert.ok((await page.locator('#case-title').innerText()).includes('<img'));assert.equal(await page.evaluate(()=>Boolean(window.evaluationXss)),false);checks.push('untrusted import rendered as text');
    const anonymous=await browser.newContext();assert.equal((await anonymous.request.get(origin+'/api/evaluation/runs')).status(),401);await anonymous.close();checks.push('anonymous API denied');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'browser-summary.json'),JSON.stringify({passed:true,checks,browserErrors:errors,left:left.counts,right:right.counts,comparison:comparison.counts,workspace:workspaceChecks,model_calls:0},null,2));
    console.log(JSON.stringify({passed:true,checks:checks.length,left:left.counts,right:right.counts,comparison:comparison.counts}));
  } catch(error) {fs.writeFileSync(path.join(output,'browser-failure.json'),JSON.stringify({passed:false,checks,errors,error:String(error)},null,2));await page.screenshot({path:path.join(output,'browser-failure.png'),fullPage:true}).catch(()=>{});throw error;}
  finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
