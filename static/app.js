/* CISP 备考助手前端：问答（SSE）+ 刷题 + 统计 */
const $ = (id) => document.getElementById(id);
function getThreadId() {
  const fallback = "web-" + Date.now();
  try {
    const key = "cisp-thread-id";
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    window.localStorage.setItem(key, fallback);
  } catch (e) { /* 隐私模式/禁用存储时退化为本页会话 */ }
  return fallback;
}
const threadId = getThreadId();

let serviceReady = false;

function setApplicationEnabled(enabled) {
  document.querySelectorAll(
    "header button, main button, main input, main select, main textarea",
  ).forEach(control => { control.disabled = !enabled; });
}

function readinessMessage(payload) {
  if (payload?.status === "migration_required") {
    const expected = payload.schema?.expected_revision || "当前版本";
    return `系统维护中：数据库迁移尚未到 ${expected}，请管理员先执行升级。`;
  }
  if (payload?.status === "assets_invalid") {
    return "系统维护中：题库或索引与清单不一致，请管理员重新校验数据资产。";
  }
  return "系统尚未就绪，请稍后重新检查。";
}

async function checkServiceReadiness() {
  const banner = $("service-status");
  const message = $("service-status-message");
  setApplicationEnabled(false);
  try {
    const response = await fetch("/api/ready", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      serviceReady = false;
      message.textContent = readinessMessage(payload);
      banner.classList.remove("hidden");
      return false;
    }
    serviceReady = true;
    banner.classList.add("hidden");
    setApplicationEnabled(true);
    return true;
  } catch (error) {
    serviceReady = false;
    message.textContent = "无法确认服务状态：" + error.message;
    banner.classList.remove("hidden");
    return false;
  }
}

async function bootstrap() {
  if (!await checkServiceReadiness()) return;
  await initDomains();
}

/* ── 页签切换 ── */
function switchTab(chat) {
  const quizWasVisible = !$("quiz-panel").classList.contains("hidden");
  $("chat-panel").classList.toggle("hidden", !chat);
  $("quiz-panel").classList.toggle("hidden", chat);
  $("tab-chat").classList.toggle("active", chat);
  $("tab-quiz").classList.toggle("active", !chat);
  if (!chat && !quizWasVisible && serviceReady) loadStats();
}
$("tab-chat").onclick = () => switchTab(true);
$("tab-quiz").onclick = () => switchTab(false);

/* ══════════ 问答 ══════════ */
function renderMarkdown(text) {
  // 先转义 HTML 防注入，再做有限 Markdown 转换（加粗/行内代码/列表/分隔线/换行）
  let t = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/^---+$/gm, "<hr>");
  t = t.replace(/^[-*] (.+)$/gm, "&bull; $1");
  t = t.replace(/\n/g, "<br>");
  return t;
}

function addBubble(cls, text) {
  const div = document.createElement("div");
  div.className = "bubble " + cls;
  div.textContent = text;
  $("chat-history").appendChild(div);
  $("chat-history").scrollTop = $("chat-history").scrollHeight;
  return div;
}

function documentUrl(source, page) {
  // #page 是 PDF 查看器的标准定位方式；服务端同时校验 page 参数。
  return `/api/document?source=${encodeURIComponent(source)}&page=${page}#page=${page}`;
}

function makeSourceLink(source, page) {
  const link = document.createElement("a");
  link.className = "source-link";
  link.href = documentUrl(source, page);
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = `《${source}》第${page}页 ↗`;
  link.title = "在新标签页打开原始课件并定位到此页";
  return link;
}

async function sendChat() {
  const input = $("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  addBubble("user", q);
  const bot = addBubble("bot", "");
  $("chat-send").disabled = true;

  const sources = [];
  try {
    const resp = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, thread_id: threadId }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let full = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "token") {
          full += ev.content;
          // 剥离模型自带的出处行（页面底部统一渲染来源，避免重复）
          bot.innerHTML = renderMarkdown(full.replace(/\n?【出处】[^\n]*$/, ""));
        }
        else if (ev.type === "contexts") {
          ev.contexts.forEach(c => sources.push(c));
        }
        $("chat-history").scrollTop = $("chat-history").scrollHeight;
      }
    }
    bot.innerHTML = renderMarkdown(full.replace(/\n?【出处】[^\n]*$/, ""));
  } catch (e) {
    bot.textContent += "\n[连接出错: " + e.message + "]";
  }
  if (sources.length) {
    const ctx = document.createElement("div");
    ctx.className = "ctx";
    ctx.append("【出处】");
    const uniqueSources = [...new Map(sources.map(c => [`${c.source}:${c.page}`, c])).values()];
    uniqueSources.slice(0, 4).forEach((c, index) => {
      if (index) ctx.append("；");
      ctx.appendChild(makeSourceLink(c.source, c.page));
    });
    bot.appendChild(ctx);
  }
  $("chat-send").disabled = false;
  $("chat-input").focus();
}
$("chat-send").onclick = sendChat;
$("chat-input").addEventListener("keydown", e => { if (e.key === "Enter") sendChat(); });

/* ══════════ 刷题 ══════════ */
let currentQuestion = null;
let answered = false;
let diagnosticSessionId = null;
let diagnosticMode = false;
let lastDiagnosticReport = null;

async function initDomains() {
  try {
    const domains = await (await fetch("/api/domains")).json();
    for (const d of domains) {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      $("quiz-domain").appendChild(opt);
      $("diagnostic-domains").appendChild(opt.cloneNode(true));
    }
  } catch (e) { /* 忽略 */ }
}

function setDiagnosticMode(enabled) {
  diagnosticMode = enabled;
  $("diagnostic-form").classList.toggle("hidden", !enabled);
  $("quiz-next").classList.toggle("hidden", enabled);
  $("diagnostic-chat").classList.toggle("hidden", !enabled || !lastDiagnosticReport);
  if (!enabled) diagnosticSessionId = null;
}

async function startDiagnostic() {
  const selected = [...$("diagnostic-domains").selectedOptions].map(o => o.value);
  if (!selected.length) {
    $("diagnostic-hint").textContent = "至少选择一个自评薄弱方向。";
    return;
  }
  $("diagnostic-start").disabled = true;
  $("diagnostic-hint").textContent = "正在建立诊断会话…";
  try {
    const resp = await fetch("/diagnostic/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        selected_domains: selected,
        self_report: {
          goal: $("diagnostic-goal").value.trim(),
          daily_minutes: Number($("diagnostic-minutes").value) || 30,
          experience: $("diagnostic-experience").value.trim(),
          domain_status: Object.fromEntries(selected.map(d => [d, "自评薄弱"])),
        },
      }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    diagnosticSessionId = (await resp.json()).session_id;
    await nextDiagnosticQuestion();
  } catch (e) {
    $("diagnostic-hint").textContent = "诊断启动失败: " + e.message;
  }
  $("diagnostic-start").disabled = false;
}

async function nextDiagnosticQuestion() {
  if (!diagnosticSessionId) return;
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "诊断选题中…";
  $("quiz-question").classList.remove("hidden");
  answered = false;
  try {
    const resp = await fetch(`/diagnostic/${diagnosticSessionId}/next`);
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (payload.status === "completed") {
      renderDiagnosticReport(payload.report);
      return;
    }
    $("diagnostic-reason").value = "";
    renderQuestion(payload.question);
    $("diagnostic-hint").textContent =
      `已完成 ${payload.progress.answered}/${payload.progress.max_questions} 题；会插入校准题。`;
  } catch (e) {
    $("q-stem").textContent = "诊断出题失败: " + e.message;
  }
}

function renderDiagnosticReport(report) {
  lastDiagnosticReport = report;
  $("quiz-question").classList.add("hidden");
  const weak = (report.weak_domains || []).join("、") || "暂无已确认薄弱域";
  const uncertain = (report.domains || []).filter(d => d.insufficient).map(d => d.domain).join("、");
  const recommendations = (report.recommendations || []).slice(0, 3).join("；");
  const misconceptions = (report.domains || []).filter(d => d.high_confidence_errors)
    .map(d => d.domain).join("、");
  const provisional = (report.knowledge_points || []).filter(p => p.provisional)
    .slice(0, 3).map(p => p.name).join("、");
  $("stats-summary").textContent = `诊断完成：已确认薄弱域 ${weak}` +
    (uncertain ? `；样本不足待验证：${uncertain}` : "") +
    (misconceptions ? `；高信心错题待澄清：${misconceptions}` : "") +
    (recommendations ? `；下一步：${recommendations}` : "") +
    (provisional ? `；知识点标签待审校：${provisional}` : "");
  const tbody = $("stats-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const d of report.domains || []) {
    const tr = document.createElement("tr");
    const mastery = d.mastery === null || d.mastery === undefined
      ? "—" : (d.mastery * 100).toFixed(0) + "%";
    const status = d.high_confidence_errors ? "⚠ 稳定误区风险"
      : d.weak ? "⚠ 薄弱" : d.low_confidence_correct ? "答对但不稳"
      : d.insufficient ? "样本不足" : "已校准";
    tr.innerHTML = `<td>${d.domain}</td><td>${d.attempts}</td><td>—</td>` +
      `<td>${mastery}</td><td>${(d.confidence * 100).toFixed(0)}%</td>` +
      `<td class="${d.weak || d.high_confidence_errors ? "weak" : ""}">${status}</td>`;
    tbody.appendChild(tr);
  }
  $("diagnostic-chat").classList.remove("hidden");
  diagnosticSessionId = null;
}

function openDiagnosticChat() {
  if (!lastDiagnosticReport) return;
  const report = lastDiagnosticReport;
  const domainEvidence = (report.domains || [])
    .map(d => `${d.domain}: ${d.attempts}题，掌握度 ${d.mastery == null ? "未知" : (d.mastery * 100).toFixed(0) + "%"}`)
    .join("；");
  const weak = (report.weak_domains || []).join("、") || "暂无已确认薄弱域";
  const calibrationEvidence = (report.domains || [])
    .filter(d => d.high_confidence_errors || d.low_confidence_correct)
    .map(d => `${d.domain}: 高信心错题${d.high_confidence_errors || 0}道，低信心答对${d.low_confidence_correct || 0}道`)
    .join("；");
  const statedReasons = (report.misconception_evidence || [])
    .filter(item => item.reason).slice(0, 3)
    .map(item => `${item.domain || "未分类"}题：${item.reason}`).join("；");
  const prompt = `我刚完成入门诊断，请基于以下结果做细致的学习访谈。已确认薄弱域：${weak}。${domainEvidence}
${calibrationEvidence ? `答题信心校准信号：${calibrationEvidence}。` : ""}${statedReasons ? `我的选择理由：${statedReasons}。` : ""}
请先围绕“概念混淆、不会应用、还是遗忘”向我追问不超过 3 个澄清问题，等我回答后再给针对性的解释、练习顺序和复习建议；不要把自评或样本不足当成确定结论。`;
  switchTab(true);
  $("chat-input").value = prompt;
  sendChat();
}

async function nextQuestion() {
  if (diagnosticMode) return nextDiagnosticQuestion();
  const mode = $("quiz-mode").value;
  const domain = $("quiz-domain").value || null;
  $("quiz-next").disabled = true;
  $("quiz-question").classList.remove("hidden");
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "加载中…";
  answered = false;
  try {
    const resp = await fetch("/quiz/next", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, domain, thread_id: threadId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    renderQuestion(await resp.json());
  } catch (e) {
    $("q-stem").textContent = "出题失败: " + e.message;
  }
  $("quiz-next").disabled = false;
}

async function choose(letter, el) {
  if (answered || !currentQuestion) return;
  answered = true;
  document.querySelectorAll(".option").forEach(o => o.classList.add("locked"));
  el.classList.add("chosen");
  let result;
  try {
    const resp = await fetch(diagnosticMode
      ? `/diagnostic/${diagnosticSessionId}/answer`
      : "/quiz/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(diagnosticMode
        ? { question_id: currentQuestion.id, choice: letter,
            confidence: Number($("diagnostic-confidence").value),
            reason: $("diagnostic-reason").value.trim() }
        : { question_id: currentQuestion.id, choice: letter, thread_id: threadId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    result = diagnosticMode ? { ...payload, ...(payload.result || {}) } : payload;
  } catch (e) {
    $("q-stem").textContent += "\n[判分失败: " + e.message + "]";
    return;
  }
  // 正确答案高亮；诊断接口同样返回 answer，保持反馈一致。
  for (const [l, text] of Object.entries(currentQuestion.options)) {
    if (result.answer.includes(l)) {
      document.querySelectorAll(".option")[ "ABCD".indexOf(l) ]?.classList.add("right");
    }
  }
  if (!result.correct) el.classList.add("wrong");
  $("q-verdict").textContent = result.correct ? "✅ 回答正确" : "❌ 回答错误";
  $("q-verdict").className = result.correct ? "ok" : "bad";
  $("q-analysis").textContent = "【解析】" + result.analysis;
  const related = $("q-related");
  related.innerHTML = "";
  const chunks = result.related_chunks || [];
  if (!chunks.length) {
    related.textContent = "（未找到关联课件）";
  }
  for (const c of chunks) {
    const item = document.createElement("div");
    item.className = "rel";
    item.appendChild(makeSourceLink(c.source, c.page));
    item.append(` · ${c.domain}`);
    item.appendChild(document.createElement("br"));
    item.append(c.text);
    related.appendChild(item);
  }
  $("q-feedback").classList.remove("hidden");
  if (diagnosticMode && result.status === "completed") {
    renderDiagnosticReport(result.report);
  } else if (diagnosticMode) {
    setTimeout(nextDiagnosticQuestion, 250);
  }
}

/* 答题后闭环：把错题带进问答页深挖 */
function deepDive() {
  if (!currentQuestion) return;
  const wrongOpt = document.querySelector(".option.chosen");
  const chosen = wrongOpt ? wrongOpt.textContent.trim().slice(0, 120) : "";
  const q = `关于这道题我答错了，请帮我讲透背后的知识点，并指出我可能混淆的概念：
「${currentQuestion.stem}」
我的选择：${chosen || "（见上）"}
请先解释正确答案为什么对，再逐个分析易混淆的干扰项。`;
  switchTab(true);
  $("chat-input").value = q;
  sendChat();
}

/* 再练一道同类题：以当前题为锚点定向生成 */
async function similarQuestion() {
  if (!currentQuestion) return;
  $("quiz-next").disabled = true;
  $("quiz-question").classList.remove("hidden");
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "正在围绕同一考点出新题（约需十几秒）…";
  answered = false;
  try {
    const resp = await fetch("/quiz/next", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "weak", domain: currentQuestion.domain,
                             anchor_question_id: currentQuestion.id,
                             thread_id: threadId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    renderQuestion(await resp.json());
  } catch (e) {
    $("q-stem").textContent = "出题失败: " + e.message;
  }
  $("quiz-next").disabled = false;
}

function renderQuestion(q) {
  currentQuestion = q.question || q;
  const question = currentQuestion;
  $("q-domain").textContent = question.domain || "未分类";
  $("q-source").textContent = question.source;
  const calibration = $("q-calibration");
  if (q.calibration_reason) {
    const progress = q.calibration_progress || {};
    calibration.textContent = `校准题：${q.calibration_reason}（已完成 ${progress.answered || 0}/${progress.required || 2} 条一致证据）`;
    calibration.classList.remove("hidden");
  } else {
    calibration.textContent = "";
    calibration.classList.add("hidden");
  }
  $("q-stem").textContent = question.stem;
  $("q-options").innerHTML = "";
  for (const [letter, text] of Object.entries(question.options)) {
    const div = document.createElement("div");
    div.className = "option";
    div.textContent = `${letter}. ${text}`;
    div.onclick = () => choose(letter, div);
    $("q-options").appendChild(div);
  }
}

function confLabel(conf) {
  if (conf === null || conf === undefined) return "—";
  return conf < 0.5 ? "低" : conf < 0.8 ? "中" : "高";
}

let activeKnowledgePointsPromise = null;

async function activeKnowledgePoints() {
  if (!activeKnowledgePointsPromise) {
    activeKnowledgePointsPromise = fetch("/api/knowledge-points?status=active")
      .then(async response => {
        if (!response.ok) throw new Error("无法读取已启用知识点");
        return response.json();
      })
      .catch(error => {
        activeKnowledgePointsPromise = null;
        throw error;
      });
  }
  return activeKnowledgePointsPromise;
}

async function showCorrectionForm(cell, item) {
  cell.textContent = "正在加载…";
  try {
    const points = await activeKnowledgePoints();
    const form = document.createElement("div");
    form.className = "attribution-correction";
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索名称、领域或 ID";
    search.setAttribute("aria-label", "搜索目标知识点");
    const select = document.createElement("select");
    select.setAttribute("aria-label", "为题目 " + item.question_id + " 选择正确知识点");
    const matchCount = document.createElement("span");
    matchCount.className = "correction-match-count";
    const sortedPoints = [...points].sort((a, b) =>
      (a.domain + "/" + a.name).localeCompare(b.domain + "/" + b.name, "zh-CN"));
    let selectedPointId = item.candidate_point_id;
    function filterPoints() {
      const query = search.value.trim().toLowerCase();
      const matches = sortedPoints.filter(point =>
        [point.name, point.domain, point.id].some(value =>
          String(value || "").toLowerCase().includes(query)));
      select.replaceChildren();
      for (const point of matches) {
        const option = document.createElement("option");
        option.value = point.id;
        option.textContent = point.domain + " / " + point.name;
        select.appendChild(option);
      }
      const selectedIndex = matches.findIndex(point => point.id === selectedPointId);
      select.selectedIndex = selectedIndex;
      if (selectedIndex < 0) selectedPointId = "";
      select.disabled = !matches.length;
      matchCount.textContent = "匹配 " + matches.length + " / " + points.length + " 个知识点";
    }
    search.addEventListener("input", filterPoints);
    select.addEventListener("change", () => { selectedPointId = select.value; });
    filterPoints();
    const reason = document.createElement("input");
    reason.maxLength = 2000;
    reason.placeholder = "纠错依据（建议填写）";
    reason.setAttribute("aria-label", "纠错依据");
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "保存纠错";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    const status = document.createElement("span");
    status.className = "correction-status";
    save.onclick = async () => {
      if (!select.value) {
        status.textContent = "请先选择目标知识点";
        return;
      }
      save.disabled = true;
      status.textContent = "正在保存…";
      try {
        const response = await fetch(
          "/api/attributions/" + encodeURIComponent(item.question_id) + "/correction",
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              thread_id: threadId,
              corrected_point_id: select.value,
              reason: reason.value.trim(),
            }),
          },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "保存失败");
        const successMessage = payload.deduplicated
          ? "归因未变化，未新增纠错事件"
          : payload.replayed_attempts
            ? "纠错已保存，并重放 " + payload.replayed_attempts + " 条画像证据"
            : payload.replacement_calibration_id
              ? "纠错已保存，并建立替代校准"
              : "纠错已保存";
        const selectedPoint = points.find(point => point.id === select.value);
        item.candidate_point_id = select.value;
        item.candidate_point_name = selectedPoint?.name || null;
        item.candidate_domain = selectedPoint?.domain || "";
        item.attribution_status = "confirmed";
        item.status = "confirmed";
        item.reason = reason.value.trim() || "用户已纠正";
        item.source = "user_correction";
        status.textContent = successMessage;
        await loadStats({refreshAttributionReview: false});
        renderAttributionReviewRows();
        $("stats-summary").textContent = successMessage + "；" + $("stats-summary").textContent;
      } catch (error) {
        status.textContent = "纠错失败：" + error.message;
        save.disabled = false;
      }
    };
    cancel.onclick = () => renderAttributionReviewRows();
    form.append(search, matchCount, select, reason, save, cancel, status);
    cell.replaceChildren(form);
  } catch (error) {
    cell.textContent = "无法纠错：" + error.message;
  }
}

async function loadAttributionObservability() {
  const panel = $("attribution-observability");
  const summary = $("attribution-observability-summary");
  const domains = $("attribution-observability-domains");
  try {
    const response = await fetch(
      `/api/attribution-observability?thread_id=${encodeURIComponent(threadId)}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.metric_semantics !== "operational_feedback_not_accuracy"
        || payload.not_accuracy_metric !== true) {
      throw new Error("口径标识缺失");
    }
    const integrity = payload.ledger_integrity || {};
    if (integrity.snapshot_scope !== "current_thread_not_event_window") {
      throw new Error("账本完整性口径缺失");
    }
    const anomalyCount = Number(integrity.anomalies_total || 0);
    panel.dataset.state = anomalyCount ? "attention" : payload.events ? "active" : "empty";
    summary.textContent = payload.events
      ? `反馈 ${payload.events} 次，涉及 ${payload.unique_questions} 道题；`
        + `重复修改 ${payload.repeat_events} 次，画像重放 ${payload.profile_replays} 次，`
        + `填写依据 ${payload.reason_provided_events} 次。`
      : "当前线程尚无纠错反馈。";
    summary.textContent += anomalyCount
      ? ` 校准账本发现 ${anomalyCount} 项结构异常，请检查下方分类。`
      : " 校准账本完整性正常。";
    const entries = Object.entries(payload.by_target_domain || {})
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
    const domainText = entries.length
      ? "目标域：" + entries.map(([domain, count]) => `${domain} ${count} 次`).join("；")
      : "目标域：暂无反馈";
    const integrityText = anomalyCount
      ? "账本异常："
        + `缺失认领 ${integrity.confirmed_missing_applied_evidence || 0}；`
        + `重复认领 ${integrity.confirmed_duplicate_applied_evidence || 0}；`
        + `意外认领 ${integrity.confirmed_unexpected_applied_evidence || 0}；`
        + `精确证据错配 ${integrity.confirmed_mismatched_applied_attempts || 0}；`
        + `非确认已入画像 ${integrity.nonconfirmed_applied_attempts || 0}；`
        + `孤儿引用 ${integrity.orphan_applied_attempts || 0}`
      : "账本异常：0";
    domains.textContent = `${domainText}；${integrityText}`;
  } catch (error) {
    panel.dataset.state = "error";
    summary.textContent = "纠错反馈暂不可用，不影响答题画像。";
    domains.textContent = "";
  }
}


const attributionReviewPageSize = 30;
const attributionReviewState = {
  items: [], cursor: null, hasMore: false, total: 0,
  query: "", loading: false, error: "", loaded: false,
};

function renderAttributionReviewRows() {
  const section = $("attribution-profile");
  const body = $("attribution-table").querySelector("tbody");
  const statusNode = $("attribution-review-status");
  const moreButton = $("attribution-load-more");
  const queryInput = $("attribution-review-query");
  const submitButton = $("attribution-review-filter").querySelector("button[type=submit]");
  const clearButton = $("attribution-review-clear");
  section.classList.toggle("hidden", !attributionReviewState.loaded);
  section.setAttribute("aria-busy", String(attributionReviewState.loading));
  queryInput.disabled = attributionReviewState.loading;
  submitButton.disabled = attributionReviewState.loading;
  clearButton.disabled = attributionReviewState.loading;
  moreButton.disabled = attributionReviewState.loading;
  moreButton.classList.toggle("hidden", !attributionReviewState.hasMore);
  moreButton.textContent = attributionReviewState.loading
    ? "正在载入…" : `加载更多题目（每批 ${attributionReviewPageSize} 道）`;

  if (attributionReviewState.error) {
    statusNode.textContent = "题目列表读取失败：" + attributionReviewState.error;
  } else if (attributionReviewState.loading) {
    statusNode.textContent = "正在读取归因审阅列表…";
  } else if (!attributionReviewState.total) {
    statusNode.textContent = attributionReviewState.query
      ? "没有匹配的题目 ID。" : "当前没有归因记录。";
  } else {
    statusNode.textContent = `已加载 ${attributionReviewState.items.length} / ${attributionReviewState.total} 道题`
      + (attributionReviewState.hasMore ? "；可继续加载" : "；已到列表末尾");
  }

  body.innerHTML = "";
  for (const item of attributionReviewState.items) {
    const tr = document.createElement("tr");
    const status = item.attribution_status || item.status || "unverified";
    const statusText = status === "confirmed" ? "已确认"
      : status === "probable" ? "待校准" : "未验证";
    const evidencePrefix = item.source === "user_correction"
      ? "用户纠错" : (item.evidence_count || 0) + " 路证据";
    const evidence = evidencePrefix + "；" + (item.reason || "无进一步说明");
    const questionCell = document.createElement("td");
    const questionId = document.createElement("span");
    questionId.className = "attribution-question-id";
    questionId.textContent = item.question_id;
    questionCell.appendChild(questionId);
    if (item.question_stem) {
      const details = document.createElement("details");
      details.className = "attribution-question-details";
      const summary = document.createElement("summary");
      summary.textContent = "查看题干和选项";
      const stem = document.createElement("p");
      stem.textContent = item.question_stem;
      details.append(summary, stem);
      const options = Object.entries(item.question_options || {});
      if (options.length) {
        const list = document.createElement("ul");
        for (const [key, value] of options) {
          const option = document.createElement("li");
          option.textContent = key + ". " + value;
          list.appendChild(option);
        }
        details.appendChild(list);
      }
      questionCell.appendChild(details);
    }
    tr.appendChild(questionCell);
    const pointCell = document.createElement("td");
    const pointName = document.createElement("span");
    pointName.className = "attribution-point-name";
    pointName.textContent = item.candidate_point_name || item.candidate_point_id || "—";
    pointCell.appendChild(pointName);
    if (item.candidate_point_name && item.candidate_point_id) {
      const pointId = document.createElement("small");
      pointId.className = "attribution-point-id";
      pointId.textContent = item.candidate_point_id;
      pointCell.appendChild(pointId);
    }
    tr.appendChild(pointCell);
    [item.candidate_domain || "—", statusText, evidence].forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 1) td.className = item.source === "user_correction"
        ? "user-correction" : status;
      tr.appendChild(td);
    });
    const action = document.createElement("td");
    const correctionButton = document.createElement("button");
    correctionButton.type = "button";
    correctionButton.className = "correction-open";
    correctionButton.textContent = item.source === "user_correction" ? "再次纠正" : "纠正";
    correctionButton.onclick = () => showCorrectionForm(action, item);
    action.appendChild(correctionButton);
    tr.appendChild(action);
    body.appendChild(tr);
  }
}

async function loadAttributionReviewPage({reset = false} = {}) {
  if (attributionReviewState.loading) return;
  const query = reset
    ? $("attribution-review-query").value.trim()
    : attributionReviewState.query;
  if (reset) {
    attributionReviewState.items = [];
    attributionReviewState.cursor = null;
    attributionReviewState.hasMore = false;
    attributionReviewState.total = 0;
    attributionReviewState.query = query;
    attributionReviewState.error = "";
    attributionReviewState.loaded = true;
  } else if (!attributionReviewState.hasMore) {
    return;
  }
  attributionReviewState.loading = true;
  attributionReviewState.error = "";
  renderAttributionReviewRows();
  const params = new URLSearchParams({
    thread_id: threadId,
    limit: String(attributionReviewPageSize),
  });
  if (attributionReviewState.cursor) params.set("after_question_id", attributionReviewState.cursor);
  if (query) params.set("question_id", query);
  try {
    const response = await fetch(`/api/attribution-review?${params}`, {cache: "no-store"});
    const page = await response.json();
    if (!response.ok) throw new Error(page.detail || "服务暂不可用");
    attributionReviewState.items = reset
      ? page.items : attributionReviewState.items.concat(page.items);
    attributionReviewState.cursor = page.next_cursor;
    attributionReviewState.hasMore = page.has_more;
    attributionReviewState.total = page.total;
  } catch (error) {
    attributionReviewState.error = error.message;
  } finally {
    attributionReviewState.loading = false;
    renderAttributionReviewRows();
  }
}

async function loadStats({refreshAttributionReview = true} = {}) {
  try {
    const s = await (await fetch(`/api/stats?thread_id=${encodeURIComponent(threadId)}`)).json();
    if (!s.total_attempts) {
      const counts = s.attribution_status_counts || {};
      const pending = (s.pending_calibrations || []).length;
      $("stats-summary").textContent = (counts.confirmed || counts.probable || counts.unverified)
        ? `还没有已确认掌握度证据；题目归因：已确认 ${counts.confirmed || 0}、待校准 ${counts.probable || 0}、未验证 ${counts.unverified || 0}`
          + (pending ? `；待校准假设 ${pending} 个` : "")
        : "还没有答题记录，先来做几道题吧！";
    } else {
      let summary = `累计答题 ${s.total_attempts} 道，总正确率 ${(s.total_accuracy * 100).toFixed(1)}%`;
      if (s.weak_domains.length) summary += `；薄弱域：${s.weak_domains.join("、")}`;
      if (s.review_due_domains.length) summary += `；复习到期：${s.review_due_domains.join("、")}`;
      if (s.review_due_cards) summary += `；待复习错题 ${s.review_due_cards} 道`;
      if (s.ask_unverified_domains && s.ask_unverified_domains.length)
        summary += `；你反复提问但还没刷题验证：${s.ask_unverified_domains.join("、")}`;
      const counts = s.attribution_status_counts || {};
      summary += `；归因：已确认 ${counts.confirmed || 0}、待校准 ${counts.probable || 0}、未验证 ${counts.unverified || 0}`;
      $("stats-summary").textContent = summary;
    }
    const calibrationOutcomes = s.attribution_evidence_channels?.calibration_outcomes || {};
    if (calibrationOutcomes.inconclusive) {
      $("stats-summary").textContent +=
        `；校准未形成结论 ${calibrationOutcomes.inconclusive} 个`;
    }
    const tbody = $("stats-table").querySelector("tbody");
    tbody.innerHTML = "";
    for (const d of s.domains || []) {
      const tr = document.createElement("tr");
      const masteryPct = d.mastery === null || d.mastery === undefined ? "—" : (d.mastery * 100).toFixed(0) + "%";
      const status = d.weak ? "⚠ 薄弱"
        : d.ask_unverified ? "❓ 提问多·未验证"
        : d.insufficient ? "样本不足"
        : d.due_for_review ? "复习到期"
        : "正常";
      const statusCls = d.weak ? "weak" : d.due_for_review ? "review" : "";
      tr.innerHTML = `<td>${d.domain}</td><td>${d.attempts}</td><td>${d.asks || 0}</td>
        <td><span class="bar" style="width:${d.mastery ? d.mastery * 80 : 0}px"></span>${masteryPct}</td>
        <td>${confLabel(d.confidence)}</td>
        <td class="${statusCls}">${status}</td>`;
      tbody.appendChild(tr);
    }
    const pointProfile = $("knowledge-point-profile");
    const pointBody = $("knowledge-point-table").querySelector("tbody");
    pointBody.innerHTML = "";
    const points = s.knowledge_points || [];
    pointProfile.classList.toggle("hidden", !points.length);
    for (const point of points.slice(0, 20)) {
      const tr = document.createElement("tr");
      const status = point.weak ? "⚠ 薄弱" : point.insufficient ? "样本不足" : "已确认";
      const values = [point.name, point.domain || "—", point.attempts,
        (point.mastery * 100).toFixed(0) + "%", confLabel(point.confidence), status];
      values.forEach((value, index) => {
        const td = document.createElement("td");
        td.textContent = value;
        if (index === 5 && point.weak) td.className = "weak";
        tr.appendChild(td);
      });
      pointBody.appendChild(tr);
    }
    if (refreshAttributionReview) await loadAttributionReviewPage({reset: true});
    await loadAttributionObservability();
  } catch (e) { /* 忽略 */ }
}

$("quiz-mode").onchange = () => setDiagnosticMode($("quiz-mode").value === "diagnostic");
$("diagnostic-start").onclick = startDiagnostic;
$("diagnostic-chat").onclick = openDiagnosticChat;
$("quiz-next").onclick = nextQuestion;
$("q-deep-dive").onclick = deepDive;
$("q-similar").onclick = similarQuestion;
$("attribution-review-filter").addEventListener("submit", event => {
  event.preventDefault();
  loadAttributionReviewPage({reset: true});
});
$("attribution-review-clear").onclick = () => {
  $("attribution-review-query").value = "";
  loadAttributionReviewPage({reset: true});
};
$("attribution-load-more").onclick = () => loadAttributionReviewPage();
$("service-retry").onclick = bootstrap;
bootstrap();
