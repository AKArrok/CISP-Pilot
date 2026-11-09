/* V2 workflow acceptance in the same real browser; synthetic labels never serve as quality evidence. */
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const path=require('node:path');

module.exports=async function({page,context,origin,output,options,checks,left,right}){
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[data-tab="compare"]').click();await page.locator('#compare-left').selectOption(left.id);await page.locator('#compare-right').selectOption(right.id);
  await page.locator('#compare-form button').click();await page.waitForFunction(()=>document.querySelector('#comparison tbody tr'));
  await page.locator('#compare-filter').selectOption('regressed');
  assert.equal(await page.locator('#comparison tbody tr').count(),3);
  await page.locator('#comparison button').filter({hasText:'ac-8-new-03'}).click();
  await page.locator('#compare-evidence').waitFor({state:'visible'});
  assert.equal(await page.locator('#compare-evidence .evidence-grid>div').count(),2);
  assert.ok((await page.locator('#compare-evidence .evidence-grid pre').first().innerText()).length>100);
  await page.locator('#compare-evidence summary').filter({hasText:'运行 trace'}).first().click();
  assert.ok((await page.locator('#compare-evidence').innerText()).includes('request_id'));
  await page.screenshot({path:path.join(output,'paired-evidence.png'),fullPage:false});
  checks.push('V2 historical regression opens paired answers, source evidence, metrics and actual trace');

  const answer='合成工程定义：执行动作A。[资料1]《合成来源》第1页';
  const result={id:'repeat-case',answer,answer_sha256:crypto.createHash('sha256').update(answer).digest('hex'),
    contexts:[{source:'合成来源',page:1,text:'合成工程定义：执行动作A。'}],checks:{recorded_execution_audit:true,citation_structure:true},
    semantic_passed:false,semantic_reviewer:'Synthetic fixture; not independent human gold',semantic_reason:'预设标签，仅验证工程行为。',
    trace:{request_id:'synthetic-workflow-request',scope:'synthetic engineering fixture'},latency_ms:2};
  const bundle={schema_version:2,name:'Synthetic repeated workflow',dataset_name:'Synthetic workflow contract',dataset_key:'synthetic-workflow-v2',
    role:'synthetic',visibility:'synthetic',criteria:'Synthetic engineering fixture; no semantic accuracy claim.',config:{model:'none'},
    artifacts:{fixture:'a'.repeat(64)},repetitions:3,cases:[{id:'repeat-case',question:'合成题：资料定义的动作是什么？',contract:{reference_evidence:'执行动作A'}}],
    results:[{...result,attempt_no:1},{...result,attempt_no:2,semantic_passed:true}],known_cost_usd:0,cost_unknown:false};
  let imported=await context.request.post(origin+'/api/evaluation/imports',{...options,data:bundle});assert.equal(imported.status(),200);const repeatedId=(await imported.json()).id;
  await page.reload();await page.waitForLoadState('networkidle');await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('[data-tab="runs"]').click();await page.locator('#run-list button').filter({hasText:bundle.name}).click();
  await page.locator('#case-list button').filter({hasText:'repeat-case'}).click();await page.locator('#attempt-select').waitFor({state:'visible'});
  assert.equal(await page.locator('#attempt-select option').count(),3);
  await page.locator('#attempt-select').selectOption('2');await page.waitForFunction(()=>document.querySelector('#case-verdict').textContent==='已通过记录');
  assert.ok((await page.locator('#attempt-metrics').innerText()).includes('semantic_support'));
  await page.locator('#attempt-select').selectOption('3');await page.waitForFunction(()=>document.querySelector('#case-verdict').textContent==='待证据');
  await page.locator('#attempt-select').selectOption('2');await page.waitForFunction(()=>document.querySelector('#case-verdict').textContent==='已通过记录');
  checks.push('V2 repeated attempt selection preserves failure, pass and missing evidence separately');
  await page.locator('#enqueue-review').click();await page.locator('#queue-list button').filter({hasText:'领取审阅'}).first().click();
  await page.locator('#queue-inspector').waitFor({state:'visible'});await page.locator('#queue-kind').selectOption('ai_assisted');await page.locator('#queue-decision').selectOption('supported');
  await page.locator('#queue-reason').fill('Synthetic browser workflow verification; no real human adjudication.');await page.locator('#queue-form button').click();
  await page.waitForFunction(()=>document.querySelector('#queue-list').textContent.includes('待人工 / 需裁决'));
  const queue=(await (await context.request.get(origin+'/api/evaluation/review-queue')).json()).items.find(item=>item.run_id===repeatedId);
  assert.equal(queue.status,'needs_adjudication');assert.equal(queue.independent_human_gold,false);
  await page.screenshot({path:path.join(output,'review-queue.png'),fullPage:true});checks.push('V2 review queue binds attempt and rubric; AI review remains pending human adjudication');

  const bulk={...bundle,name:'Synthetic offline cancellation',dataset_key:'synthetic-offline-cancel-v2',repetitions:1,cases:[],results:[]};
  for(let i=0;i<800;i++){const id=`bulk-${String(i).padStart(4,'0')}`;bulk.cases.push({id,question:'Synthetic offline workflow '+i,contract:{reference_evidence:'执行动作A'}});bulk.results.push({...result,id,attempt_no:1,semantic_passed:true});}
  imported=await context.request.post(origin+'/api/evaluation/imports',{...options,data:bulk});assert.equal(imported.status(),200);const bulkId=(await imported.json()).id;
  await page.reload();await page.waitForLoadState('networkidle');await page.locator('[data-tab="runs"]').click();await page.locator('#run-list button').filter({hasText:bulk.name}).click();
  await page.locator('#create-audit-job').click();await page.locator('#job-detail').waitFor({state:'visible'});
  await page.waitForFunction(()=>{const match=document.querySelector('#job-progress').textContent.match(/处理 (\d+)\/(\d+)/);return match&&Number(match[1])>0&&Number(match[1])<Number(match[2]);},{},{timeout:30000});
  await page.locator('#cancel-job').click();
  await page.waitForFunction(()=>document.querySelector('#job-progress').textContent.includes('已取消'),{},{timeout:30000});
  let job=(await (await context.request.get(origin+'/api/evaluation/jobs')).json()).items.find(item=>item.run_id===bulkId);
  assert.equal(job.status,'cancelled');assert.ok(job.processed>0&&job.processed<job.planned);const completed=new Map(job.results.map(item=>[item.case_id,item.created_at]));
  await page.locator('#resume-job').click();await page.waitForFunction(()=>document.querySelector('#job-progress').textContent.includes('执行完成'),{},{timeout:120000});
  job=await (await context.request.get(origin+`/api/evaluation/jobs/${job.id}`)).json();assert.equal(job.status,'completed');assert.equal(job.processed,800);assert.equal(job.failed_checks,0);assert.equal(job.errors,0);assert.equal(job.model_calls,0);
  for(const item of job.results)if(completed.has(item.case_id))assert.equal(item.created_at,completed.get(item.case_id));
  await page.screenshot({path:path.join(output,'offline-job.png'),fullPage:true});checks.push('V2 real offline auditor cancellation and resume retain completed rows without duplicate execution');
  return {repeated_run_id:repeatedId,queue_id:queue.id,job_id:job.id,processed:job.processed,processed_before_cancel:completed.size,model_calls:0};
};
