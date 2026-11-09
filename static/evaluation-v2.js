'use strict';
// V2 adds version/attempt detail and workflows to the existing authenticated workspace.
const v2 = {ready:false, job:null, jobOffset:0, queueOffset:0, claim:null, decision:null, comparison:null, compareRequest:0, jobRequest:null};
const workflowLabels = {queued:'待执行',running:'执行中',completed:'执行完成',failed:'执行不完整',cancelled:'已取消',interrupted:'已中断',pending:'待审',in_review:'审阅中',reviewed:'已审',needs_adjudication:'待人工 / 需裁决',resolved:'已裁决'};
const dimensionLabels = {retrieval:'检索',citation:'引用',semantic:'语义',abstention:'拒答范围',execution:'执行'};
function control(label, fn, cls='quiet') {const button=node('button',label,cls);button.type='button';button.onclick=action(fn);return button;}
function jsonDetails(label, value) {const details=node('details');details.append(node('summary',label),node('pre',text(value)));return details;}
function statusText(status) {return labels[status] || workflowLabels[status] || status;}
function metricText(metric) {return metric ? `${statusText(metric.status)} · ${metric.value ?? '未评分'}\n${metric.kind} / ${metric.evaluator_version}\n${metric.reason || '无附加说明'}` : '未记录';}

const runActions=$('run-workspace').querySelector('.actions');
const startJob=control('创建离线审计任务',async()=>{
  if(!state.run) return;startJob.disabled=true;
  try {if(!v2.jobRequest || v2.jobRequest.run!==state.run.id)v2.jobRequest={run:state.run.id,id:crypto.randomUUID()};
    const job=await api(`evaluation/runs/${state.run.id}/jobs`,{method:'POST',body:JSON.stringify({request_id:v2.jobRequest.id,command:'saved-http-audit-v1'})});
    v2.jobRequest=null;tab('jobs');await showJob(job.id);await loadJobs();message('离线任务已创建；逐题使用现有引用审计器，不调用模型。');
  }finally{startJob.disabled=false;}
});startJob.id='create-audit-job';startJob.hidden=true;runActions.prepend(startJob);
const attemptPanel=node('div',undefined,'panel attempt-toolbar');attemptPanel.id='attempt-panel';attemptPanel.hidden=true;
const attemptLabel=node('label','运行次数'), attemptSelect=node('select');attemptSelect.id='attempt-select';attemptLabel.append(attemptSelect);
const enqueueButton=control('加入审阅队列',async()=>{const item=await api(`evaluation/runs/${state.run.id}/cases/${encodeURIComponent(state.case.case.id)}/attempts/${state.case.attempt_no}/queue`,{method:'POST'});tab('review-queue');await loadQueue();message(`已加入审阅队列：${item.id.slice(0,12)}。没有真实人工复核时保留待审状态。`);});enqueueButton.id='enqueue-review';
attemptPanel.append(attemptLabel,enqueueButton);$('case-verdict').after(attemptPanel);
const scorePanel=node('div');scorePanel.id='attempt-metrics';attemptPanel.after(scorePanel);
const tracePanel=node('div');tracePanel.id='attempt-trace';scorePanel.after(tracePanel);
const compareControls=node('div',undefined,'filters');const compareFilterLabel=node('label','比较筛选'),compareFilter=node('select');compareFilter.id='compare-filter';
for(const [value,label]of [['all','全部'],['regressed','只看退化'],['fixed','只看修复'],['reason_changed','总体状态未变但分项变化']]){const option=node('option',label);option.value=value;compareFilter.append(option);}compareFilterLabel.append(compareFilter);compareControls.append(compareFilterLabel);$('compare-form').after(compareControls);
const compareEvidence=node('section',undefined,'panel');compareEvidence.id='compare-evidence';compareEvidence.hidden=true;$('comparison').after(compareEvidence);
const oldCompare=$('compare-form').onsubmit,oldReview=$('review-form').onsubmit;

window.evaluationV2={
  async refresh(){
    const capabilities=await api('evaluation/capabilities');v2.ready=capabilities.schema_version===2;
    $('jobs-nav').hidden=$('queue-nav').hidden=startJob.hidden=!v2.ready;
    if(!v2.ready){message('当前测评库为第一版；显式升级后可使用逐次结果、离线任务和审阅队列。');return;}
    const datasets=await api('evaluation/datasets');
    table($('dataset-list'),['数据集 / 稳定身份','版本 / 父版本','角色','案例数','实验数','判据版本'],datasets.items.map(item=>{
      const run=state.runs.find(run=>run.version_id===item.version_id),button=run?control(item.name,()=>openRun(run.id),'row-button'):node('span',item.name);
      const name=node('div');name.append(button,node('div',item.id.slice(0,14),'muted'));
      return [name,`${item.version_id.slice(0,14)}\n父版本 ${item.parent_id?.slice(0,14)||'未指定'}\n${item.label}`,item.role,item.cases,item.experiments,item.rubric_id.slice(0,14)];
    }));
    if(!capabilities.worker_enabled)message('测评库已升级；后台离线worker未启用，任务将保持排队。请按使用说明启用后重启服务。');
  },
  inspect(data){
    attemptPanel.hidden=!data.attempts;scorePanel.replaceChildren();tracePanel.replaceChildren();
    if(!data.attempts)return;
    attemptSelect.replaceChildren();for(const item of data.attempts){const option=node('option',`第 ${item.attempt_no} 次 · ${statusText(item.status)}`);option.value=item.attempt_no;attemptSelect.append(option);}attemptSelect.value=data.attempt_no;
    enqueueButton.disabled=!data.result;
    table(scorePanel,['分项','指标 / 类型','状态 / 值','版本与判据','依据'],data.metrics.map(metric=>[dimensionLabels[metric.dimension],`${metric.id}\n${metric.kind}`,`${statusText(metric.status)} / ${metric.value ?? '未评分'}`,`${metric.evaluator_version}\n${metric.rubric_id.slice(0,12)}`,metric.reason]));
    const result=data.result;
    tracePanel.append(node('p',`案例版本 ${data.case_version.slice(0,16)} · 判据 ${data.rubric_id.slice(0,16)} · 耗时 ${result?.latency_ms == null?'未记录':result.latency_ms.toFixed(2)+' ms'} · 输入/输出token ${result?.prompt_tokens ?? '未知'} / ${result?.completion_tokens ?? '未知'} · 单次费用 ${result?.known_cost_usd == null?'未知':'$'+result.known_cost_usd}`,'muted'));
    tracePanel.append(jsonDetails('运行 trace / 阶段事件（原始导入快照）',result?.trace && Object.keys(result.trace).length?result.trace:{status:'未记录，未补造历史追踪'}));
  }
};
attemptSelect.onchange=action(()=>openCase(state.case.case.id,Number(attemptSelect.value)));
$('review-form').onsubmit=action(async event=>{
  if(!v2.ready)return oldReview(event);event.preventDefault();const button=event.submitter;button.disabled=true;
  try {const current=state.case,body={request_id:state.reviewRequest||crypto.randomUUID(),answer_sha256:current.result.answer_sha256,rubric_id:current.rubric_id,decision:$('review-decision').value,reason:$('review-reason').value,kind:$('review-kind').value};state.reviewRequest=body.request_id;
    await api(`evaluation/runs/${state.run.id}/cases/${encodeURIComponent(current.case.id)}/attempts/${current.attempt_no}/reviews`,{method:'POST',body:JSON.stringify(body)});await openCase(current.case.id,current.attempt_no);message('复核已追加并绑定答案、运行次数及判据；原始评分没有改变。');
  }finally{button.disabled=false;}
});

function drawComparison(){
  const data=v2.comparison;if(!data)return;const holder=$('comparison');holder.replaceChildren();
  holder.append(node('p',Object.entries(data.counts).map(([key,value])=>`${labels[key]} ${value}`).join(' / ')));
  holder.append(node('p','“未变”仅表示跨重复次数汇总后的总体状态未变；分项和失败原因可能变化。','muted'));
  holder.append(jsonDetails('逐字段配置差异',data.config_diff.length?data.config_diff:'配置相同'));
  const filtered=data.items.filter(item=>compareFilter.value==='all'||(compareFilter.value==='reason_changed'?item.change==='unchanged'&&item.reason_changed:item.change===compareFilter.value));
  const tableHolder=node('div');holder.append(tableHolder);
  table(tableHolder,['题号 / 打开证据','问题','基线 / 次数','候选 / 次数','变化'],filtered.map(item=>[
    control(item.id,()=>showComparison(item.id,1,1),'row-button'),item.question,`${statusText(item.before)} / ${item.left_attempts}`,`${statusText(item.after)} / ${item.right_attempts}`,`${labels[item.change]}${item.reason_changed?' · 分项有变化':''}`]));
}
$('compare-form').onsubmit=action(async event=>{if(!v2.ready)return oldCompare(event);event.preventDefault();v2.comparison=await api('evaluation/compare?'+new URLSearchParams({left:$('compare-left').value,right:$('compare-right').value}));compareEvidence.hidden=true;drawComparison();});
compareFilter.onchange=drawComparison;
async function showComparison(caseId,leftAttempt,rightAttempt){
  const request=++v2.compareRequest,comparison=v2.comparison;
  const data=await api('evaluation/compare-detail?'+new URLSearchParams({left:comparison.left,right:comparison.right,case_id:caseId,left_attempt:leftAttempt,right_attempt:rightAttempt}));if(request!==v2.compareRequest)return;
  compareEvidence.replaceChildren();compareEvidence.hidden=false;compareEvidence.append(node('p','PAIRED EVIDENCE / 同题逐次对照','eyebrow'),node('h3',`${caseId} · ${data.left?.case.question || data.right.case.question}`),node('p',data.comparable?'输入契约和判据一致；评分仅为原始记录。':'输入契约、角色或判据不同，不能将差异计为修复。','muted'));
  const grid=node('div',undefined,'evidence-grid');
  for(const side of ['left','right']){const detail=data[side],column=node('div');column.append(node('h4',side==='left'?'基线版本':'候选版本'));
    if(!detail){column.append(node('p','该版本没有这道题。','muted'));grid.append(column);continue;}
    const select=node('select');select.id=`compare-${side}-attempt`;for(const item of detail.attempts){const option=node('option',`第 ${item.attempt_no} 次 · ${statusText(item.status)}`);option.value=item.attempt_no;select.append(option);}select.value=detail.attempt_no;
    select.onchange=action(()=>showComparison(caseId,Number(side==='left'?select.value:leftAttempt),Number(side==='right'?select.value:rightAttempt)));
    const label=node('label','重复运行');label.append(select);column.append(label,badge(detail.status),node('pre',detail.result?.answer??'没有记录回答。'));
    column.append(jsonDetails('检索来源与引用片段',detail.result?.contexts??[]),jsonDetails('运行 trace / 逐次用量',detail.result?.trace??{status:'未记录'}),jsonDetails('冻结题目契约 / 参考依据',detail.case.contract));grid.append(column);
  }compareEvidence.append(grid);
  const scoreTable=node('div');compareEvidence.append(node('h4',`分项对照 · ${data.reason_changed?'存在变化':'未记录分项变化'}`),scoreTable);
  table(scoreTable,['指标','基线状态 / 依据','候选状态 / 依据','变化'],data.metrics.map(row=>[row.id,metricText(row.before),metricText(row.after),row.changed?'有变化':'相同']));
  compareEvidence.append(jsonDetails('回答文本差异',data.answer_diff||'文本相同 / 无可比较文本'));compareEvidence.scrollIntoView({behavior:'smooth',block:'start'});
}

async function loadJobs(){if(!v2.ready)return;const data=await api('evaluation/jobs');table($('jobs-list'),['任务','状态','进度','未通过检查 / 执行错误','创建时间'],data.items.map(job=>[control(job.id.slice(0,12),()=>showJob(job.id),'row-button'),`${statusText(job.status)}${job.cancel_requested&&job.status==='running'?' · 正在取消':''}`,`${job.processed} / ${job.planned}`,`${job.failed_checks} / ${job.errors}`,time(job.created_at)]));}
async function showJob(id){const job=await api(`evaluation/jobs/${id}`);if(v2.job!==id)v2.jobOffset=0;v2.job=id;$('job-detail').hidden=false;$('job-title').textContent=`${job.command} · ${job.id.slice(0,12)}`;
  $('job-progress').textContent=`${statusText(job.status)} · 处理 ${job.processed}/${job.planned} · 未通过检查 ${job.failed_checks} · 执行错误/缺失 ${job.errors} · 模型调用 0${job.error?' · '+job.error:''}`;
  $('cancel-job').disabled=!['queued','running','interrupted'].includes(job.status);$('resume-job').disabled=!['failed','cancelled','interrupted'].includes(job.status)||job.processed===job.planned;
  table($('job-results'),['题号','次数','审计结果','检查与原始审计输出'],job.results.slice(v2.jobOffset,v2.jobOffset+25).map(row=>[row.case_id,row.attempt_no,statusText(row.status),jsonDetails('展开审计记录',row.report)]));
  const pages=node('div',undefined,'pagination'),previous=control('上一页',async()=>{v2.jobOffset=Math.max(0,v2.jobOffset-25);await showJob(id);}),next=control('下一页',async()=>{v2.jobOffset+=25;await showJob(id);});previous.disabled=v2.jobOffset===0;next.disabled=v2.jobOffset+25>=job.results.length;pages.append(previous,node('span',`${job.results.length? v2.jobOffset+1:0}–${Math.min(v2.jobOffset+25,job.results.length)} / ${job.results.length}`),next);$('job-results').append(pages);
  return job;
}
$('refresh-jobs').onclick=action(loadJobs);$('cancel-job').onclick=action(async()=>{await api(`evaluation/jobs/${v2.job}/cancel`,{method:'POST'});await showJob(v2.job);await loadJobs();});$('resume-job').onclick=action(async()=>{await api(`evaluation/jobs/${v2.job}/resume`,{method:'POST'});await showJob(v2.job);await loadJobs();});

async function loadQueue(){if(!v2.ready)return;const data=await api('evaluation/review-queue?'+new URLSearchParams({status:$('queue-filter').value,offset:v2.queueOffset,limit:20}));
  table($('queue-list'),['题目 / 批次','次数','状态','领取账号','操作'],data.items.map(item=>[
    `${item.case_id} · ${item.question}\n${item.run_name}`,item.attempt_no,statusText(item.status),item.assignee||'未领取',
    ['reviewed','resolved'].includes(item.status)?node('span','已保留复核结果','muted'):item.status==='needs_adjudication'&&item.first_reviewer===String(state.user.id)?node('span','等待另一复核者','muted'):item.assignee&&item.assignee!==String(state.user.id)&&item.locked_until*1000>Date.now()?node('span','其他账号审阅中','muted'):control(item.status==='needs_adjudication'?'领取裁决':'领取审阅',()=>claimQueue(item))]));
  $('queue-page').textContent=`${data.total? v2.queueOffset+1:0}–${Math.min(v2.queueOffset+20,data.total)} / ${data.total}`;$('queue-prev').disabled=v2.queueOffset===0;$('queue-next').disabled=v2.queueOffset+20>=data.total;
}
async function claimQueue(item){const claim=await api(`evaluation/review-queue/${item.id}/claim`,{method:'POST'});v2.claim={...item,...claim};v2.decision=null;
  const detail=await api(`evaluation/runs/${item.run_id}/cases/${encodeURIComponent(item.case_id)}?attempt=${item.attempt_no}`);v2.claim.detail=detail;
  $('queue-inspector').hidden=false;$('queue-title').textContent=`${item.case_id} · 第 ${item.attempt_no} 次`;$('queue-context').textContent=`${statusText(claim.status)} · 已领取15分钟 · 判据 ${claim.rubric_id.slice(0,16)}`;
  $('queue-rubric').textContent=item.criteria;$('queue-answer').textContent=detail.result.answer;
  $('queue-history').replaceChildren();for(const record of detail.reviews)$('queue-history').append(node('div',`${record.payload.kind} · ${record.payload.decision}\n${record.payload.reason}`,'review-entry'));
  $('queue-kind').value=claim.status==='needs_adjudication'?'human':'ai_assisted';$('queue-kind').disabled=claim.status==='needs_adjudication';$('queue-reason').value='';await loadQueue();
}
$('queue-form').onsubmit=action(async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const claim=v2.claim;
  const body={answer_sha256:claim.detail.result.answer_sha256,rubric_id:claim.rubric_id,lock_token:claim.lock_token,decision:$('queue-decision').value,reason:$('queue-reason').value,kind:$('queue-kind').value,action:claim.status==='needs_adjudication'?'adjudicate':'review'};
  const key=JSON.stringify(body);if(!v2.decision||v2.decision.key!==key)v2.decision={key,request_id:crypto.randomUUID()};
  const result=await api(`evaluation/review-queue/${claim.id}/complete`,{method:'POST',body:JSON.stringify({...body,request_id:v2.decision.request_id})});$('queue-inspector').hidden=true;await loadQueue();message(`已保存：${statusText(result.status)}。原判保持不变；不自动晋级为独立人工金标。`);
 }finally{button.disabled=false;}});
$('queue-filter').onchange=action(async()=>{v2.queueOffset=0;await loadQueue();});$('queue-prev').onclick=action(async()=>{v2.queueOffset=Math.max(0,v2.queueOffset-20);await loadQueue();});$('queue-next').onclick=action(async()=>{v2.queueOffset+=20;await loadQueue();});
document.querySelector('[data-tab="jobs"]').addEventListener('click',action(loadJobs));document.querySelector('[data-tab="review-queue"]').addEventListener('click',action(loadQueue));
let polling=false;
setInterval(async()=>{if(polling||!v2.ready||$('workspace').hidden||$('jobs').hidden)return;polling=true;try{await loadJobs();if(v2.job)await showJob(v2.job);}catch(error){message(error.message,true);}finally{polling=false;}},1200);
