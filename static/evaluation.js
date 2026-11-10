'use strict';
const $ = id => document.getElementById(id);
const state = {csrf: '', runs: [], run: null, case: null, offset: 0, total: 0, reviewRequest: null};
const labels = {passed:'已通过记录', failed:'失败', unknown:'待证据', fixed:'修复', regressed:'退化', unchanged:'未变', incomparable:'不可比较', insufficient_evidence:'证据不足', added:'新增', removed:'移除'};
function message(text, error = false) { $('notice').textContent = text; $('notice').classList.toggle('error', error); }
function text(value) { return value === null || value === undefined ? '未记录' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value); }
function node(tag, content, cls) { const n = document.createElement(tag); if (content !== undefined) n.textContent = text(content); if (cls) n.className = cls; return n; }
function badge(status) { return node('span', labels[status] || status, 'badge ' + status); }
function time(value) { return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short'}).format(new Date(value)); }
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.method && options.method !== 'GET') { headers['X-CSRF-Token'] = state.csrf; headers['Content-Type'] = 'application/json'; }
  const response = await fetch('/api/' + path, {...options, headers});
  if (!response.ok) {
    if (response.status === 401) { $('workspace').hidden = true; $('login-panel').hidden = false; $('session').hidden = true; }
    const payload = await response.json().catch(() => ({}));
    const friendly = {401:'登录已失效，请重新登录。',403:'无访问权限或会话校验失败。请核对测评账号名单与登录状态。',413:'文件超过 8 MiB。',422:'数据校验失败：请核对格式、题号、答案哈希，或复核请求是否冲突。',503:'服务未就绪，请检查隔离数据库迁移与资产配置。'};
    throw new Error(friendly[response.status] || text(payload.detail || `请求失败 (${response.status})`));
  }
  if (response.status === 204) return null;
  return response.json();
}
function action(fn) { return async event => { try { await fn(event); } catch (error) { message(error.message, true); } }; }
function tab(name) { for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== name; for (const button of document.querySelectorAll('[data-tab]')) button.classList.toggle('active', button.dataset.tab === name); }
document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => tab(button.dataset.tab));
function table(container, headings, rows) {
  container.replaceChildren();
  if (!rows.length) { container.append(node('p','暂无匹配记录。','muted')); return; }
  const wrap = node('div',undefined,'table-wrap'), t = node('table'), head = node('thead'), tr = node('tr');
  headings.forEach(label => tr.append(node('th',label))); head.append(tr); t.append(head);
  const body = node('tbody');
  rows.forEach(cells => { const row = node('tr'); cells.forEach(value => { const cell = node('td'); cell.append(value instanceof Node ? value : document.createTextNode(text(value))); row.append(cell); }); body.append(row); });
  t.append(body); wrap.append(t); container.append(wrap);
}
function runButton(run) { const button = node('button',run.name,'row-button'); button.onclick = action(() => openRun(run.id)); return button; }
function runRows(runs) { return runs.map(run => [runButton(run),run.planned,`${run.counts.failed} / ${run.planned}`,`${run.counts.unknown} / ${run.planned}`,time(run.created_at)]); }
async function refresh() {
  const payload = await api('evaluation/runs'); state.runs = payload.items; state.user = payload.user;
  $('username').textContent = payload.user.username; $('session').hidden = false; $('login-panel').hidden = true; $('workspace').hidden = false;
  const totals = state.runs.reduce((sum, run) => ({planned:sum.planned+run.planned, failed:sum.failed+run.counts.failed, unknown:sum.unknown+run.counts.unknown}), {planned:0,failed:0,unknown:0});
  $('metrics').replaceChildren();
  for (const [label, value, note] of [['评测批次',state.runs.length,'已导入并保留原始判定'],['失败记录',totals.failed,`分母 ${totals.planned} 条批次内题目记录`],['待补充证据',totals.unknown,'未记录结果或缺少分项判定'],['费用记录未知',state.runs.filter(r=>r.cost_unknown || r.known_cost_usd === null).length,'单位：批次；非零费用估算']]) {
    const card = node('div',undefined,'metric'); card.append(node('span',label),node('strong',value),node('span',note)); $('metrics').append(card);
  }
  const headings = ['批次','题目数','失败 / 总数','待证据 / 总数','导入时间（上海）'];
  table($('recent'),headings,runRows(state.runs.slice(0,5))); table($('run-list'),headings,runRows(state.runs));
  const groups = new Map(); for (const run of state.runs) { if (!groups.has(run.dataset_id)) groups.set(run.dataset_id,{...run,batches:0}); groups.get(run.dataset_id).batches++; }
  table($('dataset-list'),['数据集（查看题目）','版本摘要','角色','题目','批次'],[...groups.values()].map(r=>{const b=node('button',r.dataset_name,'row-button');b.onclick=action(()=>openRun(r.id));return [b,r.dataset_id.slice(0,16),r.role,r.planned,r.batches];}));
  for (const id of ['compare-left','compare-right']) {
    const previous = $(id).value; $(id).replaceChildren();
    state.runs.forEach(run => { const option = node('option',run.name); option.value = run.id; $(id).append(option); });
    if (state.runs.some(run=>run.id===previous)) $(id).value=previous;
  }
  if (state.runs.length > 1 && $('compare-left').value === $('compare-right').value) $('compare-left').selectedIndex = 1;
  if (window.evaluationV2) await window.evaluationV2.refresh();
  if (window.dataPlatform) await window.dataPlatform.refresh();
}
$('login').onsubmit = action(async event => { event.preventDefault(); const data = await api('auth/login',{method:'POST',body:JSON.stringify({username:$('login-name').value,password:$('login-password').value})}); state.csrf = data.csrf_token; $('login-password').value=''; await refresh(); message('已登录。请选择评测批次或导入历史记录。'); });
$('logout').onclick = action(async () => { await api('auth/logout',{method:'POST'}); location.reload(); });
$('import-file').onchange = action(async event => {
  const file = event.target.files[0]; if (!file) return;
  try { if (file.size>8*1024*1024) throw new Error('文件超过 8 MiB。'); const result=await api('evaluation/imports',{method:'POST',body:await file.text()}); await refresh(); await openRun(result.id); message(result.created?'导入成功；原始判定已保留。':'该评测包已存在，未重复新增。'); } finally { event.target.value=''; }
});
async function openRun(id) {
  state.run=await api('evaluation/runs/'+id); state.offset=0; state.case=null;
  $('case-detail').hidden=true; $('status-filter').value='all'; $('search').value=''; $('run-workspace').hidden=false; tab('runs');
  $('run-title').textContent=state.run.name;
  $('run-meta').textContent=`${state.run.role} · ${state.run.visibility} · ${state.run.recorded}/${state.run.planned} 条结果 · 已知估算 ${state.run.known_cost_usd === null ? '未记录' : '$'+state.run.known_cost_usd.toFixed(7)} · 未知费用：${state.run.cost_unknown?'是':'否'}`;
  $('run-config').textContent=text({run_id:state.run.id,dataset_id:state.run.dataset_id,criteria:state.run.criteria,config:state.run.config,artifacts:state.run.artifacts}); await loadCases();
}
async function loadCases() {
  const runId=state.run.id, params=new URLSearchParams({status:$('status-filter').value,query:$('search').value,offset:state.offset,limit:20});
  const data=await api(`evaluation/runs/${runId}/cases?${params}`); if (state.run.id!==runId) return;
  state.total=data.total;
  table($('case-list'),['题号','问题','原始判定','通过次数 / 计划次数'],data.items.map(item=>{const b=node('button',item.id,'row-button'); b.onclick=action(()=>openCase(item.id)); return [b,item.question,badge(item.status),`${item.passed_attempts ?? (item.status==='passed'?1:0)} / ${item.attempts ?? 1}`];}));
  $('page-label').textContent=`${data.total ? state.offset+1 : 0}–${Math.min(state.offset+20,data.total)} / ${data.total}`; $('previous').disabled=state.offset===0; $('next').disabled=state.offset+20>=data.total;
}
$('status-filter').onchange=action(async()=>{state.offset=0; await loadCases();});
$('search-button').onclick=action(async()=>{state.offset=0; await loadCases();});
$('search').onkeydown=event=>{if(event.key==='Enter') $('search-button').click();};
$('previous').onclick=action(async()=>{state.offset=Math.max(0,state.offset-20);await loadCases();});
$('next').onclick=action(async()=>{state.offset+=20;await loadCases();});
async function openCase(id, attempt=1) {
  const runId=state.run.id, data=await api(`evaluation/runs/${runId}/cases/${encodeURIComponent(id)}?attempt=${attempt}`); if(state.run.id!==runId) return;
  state.case=data; state.reviewRequest=null; $('case-detail').hidden=false; $('case-title').textContent=`${id} · ${data.case.question}`; $('case-verdict').replaceChildren(badge(data.status));
  $('answer').textContent=data.result ? data.result.answer : '未记录实际回答。';
  $('original-review').textContent=data.result ? text({checks:data.result.checks,semantic_passed:data.result.semantic_passed,reviewer:data.result.semantic_reviewer || '未记录',reason:data.result.semantic_reason || '未记录',answer_sha256:data.result.answer_sha256,independent_human_gold:false}) : '缺少执行结果；不作通过判定。';
  $('case-contract').textContent=text(data.case.contract); $('contexts').replaceChildren();
  if (!data.result?.contexts.length) $('contexts').append(node('p','没有记录检索片段。','muted'));
  for(const context of data.result?.contexts || []) {const details=node('details');details.append(node('summary',`${context.source || '来源未记录'} · ${context.locator || context.page || '位置未记录'}`),node('pre',text(context)));$('contexts').append(details);}
  $('review-list').replaceChildren();
  for(const review of data.reviews) $('review-list').append(node('div',`${review.payload.kind==='human'?'人工复核':'AI 辅助复核'} · ${review.payload.decision} · ${time(review.created_at)}\n账号 ${review.actor}\n${review.payload.reason}`,'review-entry'));
  $('review-form').hidden=!data.result; $('review-reason').value='';
  if (window.evaluationV2) window.evaluationV2.inspect(data);
  if (window.dataPlatform) window.dataPlatform.inspect(data);
  $('case-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
$('review-form').onsubmit=action(async event=>{
  event.preventDefault(); const button=event.submitter; if(button.disabled) return; button.disabled=true;
  try { const id=state.case.case.id; const body={request_id:state.reviewRequest || crypto.randomUUID(),answer_sha256:state.case.result.answer_sha256,decision:$('review-decision').value,reason:$('review-reason').value,kind:$('review-kind').value};state.reviewRequest=body.request_id;await api(`evaluation/runs/${state.run.id}/cases/${encodeURIComponent(id)}/reviews`,{method:'POST',body:JSON.stringify(body)});await openCase(id);message('复核已追加。原始判定保持保留。');} finally {button.disabled=false;}
});
$('check-run').onclick=action(async event=>{event.target.disabled=true;try {const data=await api(`evaluation/runs/${state.run.id}/checks`,{method:'POST'});message(`离线完整性检查：${data.payload.status}；${text(data.payload.checks)}\n模型调用 0；此检查不判断答案语义正确性。`);}finally{event.target.disabled=false;}});
async function download(format) {
  const response=await fetch(`/api/evaluation/runs/${state.run.id}/export?format=${format}`);if(!response.ok)throw new Error('报告导出失败，请核对登录状态。');
  const url=URL.createObjectURL(await response.blob()), link=node('a');link.href=url;link.download=`evaluation-${state.run.id.slice(0,12)}.${format==='json'?'json':'md'}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('已导出脱敏报告：不含问题、回答、原文片段、自由文本复核或配置。');
}
$('export-json').onclick=action(()=>download('json'));$('export-md').onclick=action(()=>download('markdown'));
$('compare-form').onsubmit=action(async event=>{
  event.preventDefault();const data=await api('evaluation/compare?'+new URLSearchParams({left:$('compare-left').value,right:$('compare-right').value}));
  const holder=$('comparison');holder.replaceChildren();holder.append(node('p',`配置变化：${data.config_changed?'有':'无'}；判据与角色一致：${data.criteria_match?'是':'否'}。`,'muted'));
  const counts=node('p');counts.textContent=Object.entries(data.counts).map(([key,value])=>`${labels[key]} ${value}`).join(' / ');holder.append(counts);const rows=node('div');holder.append(rows);
  table(rows,['题号','基线','候选','变化'],data.items.map(item=>[item.id,badge(item.before),badge(item.after),labels[item.change]]));
});
(async()=>{try {const data=await api('auth/me');state.csrf=data.csrf_token;await refresh();}catch(error){if(state.csrf)message(error.message,true);}})();
