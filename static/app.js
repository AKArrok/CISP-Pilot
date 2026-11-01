/* CISP 备考助手前端：问答（SSE）+ 刷题 + 统计 */
const $ = (id) => document.getElementById(id);
let threadId = null;
let currentUser = null;
let csrfToken = null;
let serviceReady = false;
let sessionEpoch = 0;
let sessionController = new AbortController();
let authBusy = false;
let pendingRemoteChange = false;
let pendingIdentityCheck = false;
let chatHistoryLoading = false;
const authChannel = typeof BroadcastChannel === "undefined"
  ? null : new BroadcastChannel("cisp-session-changed");
const authStorageKey = "cisp-session-changed";

function broadcastSessionChange() {
  if (authChannel) authChannel.postMessage({ type: "session-changed" });
  else {
    try { localStorage.setItem(authStorageKey, String(Date.now()) + Math.random()); }
    catch (_) { /* 存储不可用时仍可通过焦点复核身份 */ }
  }
}

function advanceSession() {
  sessionController.abort();
  sessionController = new AbortController();
  sessionEpoch += 1;
}

async function apiFetch(url, options = {}) {
  const epoch = sessionEpoch;
  const headers = new Headers(options.headers || {});
  if (csrfToken && !["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(url, {
    ...options, headers, credentials: "same-origin",
    signal: options.signal
      ? AbortSignal.any([sessionController.signal, options.signal]) : sessionController.signal,
  });
  if (epoch !== sessionEpoch) throw new DOMException("会话已切换", "AbortError");
  if (response.status === 401 && currentUser && url !== "/api/auth/me") {
    clearIdentity();
    $("auth-status").textContent = "登录已失效，请重新登录。";
    showAccount();
    broadcastSessionChange();
    throw new DOMException("登录已失效", "AbortError");
  }
  return response;
}

async function responsePayload(response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
  return payload;
}

function resetBusinessState() {
  resetRequestTrace();
  chatHistoryLoading = false;
  resetDiagnosticRequests();
  resetDiagnosticList();
  threadId = null;
  currentQuestion = null;
  quizSubmission = null;
  answered = false;
  diagnosticSessionId = null;
  diagnosticMode = false;
  lastDiagnosticReport = null;
  activeKnowledgePointsPromise = null;
  Object.assign(attributionReviewState, {
    loaded: false, loading: false, items: [], cursor: null, hasMore: false,
    total: 0, query: "", error: "",
  });
  for (const id of ["chat-history", "q-options", "stats-summary", "attribution-review-status",
                    "attribution-observability-summary", "attribution-observability-domains",
                    "q-stem", "q-domain", "q-source", "q-verdict", "q-analysis", "q-related"])
    $(id).replaceChildren();
  for (const id of ["stats-table", "knowledge-point-table", "attribution-table"])
    $(id).querySelector("tbody").replaceChildren();
  for (const id of ["chat-input", "diagnostic-goal", "diagnostic-experience", "diagnostic-reason",
                    "attribution-review-query"]) $(id).value = "";
  $("quiz-question").classList.add("hidden");
  $("q-feedback").classList.add("hidden");
  $("knowledge-point-profile").classList.add("hidden");
  $("diagnostic-chat").classList.add("hidden");
  $("attribution-observability").removeAttribute("data-state");
  $("quiz-mode").value = "random";
  $("quiz-domain").replaceChildren(new Option("全部", ""));
  $("diagnostic-domains").replaceChildren();
  $("diagnostic-hint").textContent = "自评只作为先验，系统会用校准题验证。";
  setDiagnosticMode(false);
  renderAttributionReviewRows();
  setApplicationEnabled(false);
}

function showAccount() {
  $("auth-form").classList.toggle("hidden", !!currentUser);
  $("account-session").classList.toggle("hidden", !currentUser);
  $("account-name").textContent = currentUser?.username || "";
  $("auth-form").querySelectorAll("input, button").forEach(control => {
    control.disabled = !serviceReady;
  });
  setApplicationEnabled(serviceReady && !!currentUser && !!threadId);
}

function clearIdentity() {
  advanceSession();
  currentUser = null;
  csrfToken = null;
  resetBusinessState();
  $("thread-select").replaceChildren();
  showAccount();
}

async function verifyIdentity(clearFirst = false) {
  if (authBusy) {
    if (clearFirst) pendingRemoteChange = true;
    else pendingIdentityCheck = true;
    return;
  }
  const hadUser = !!currentUser;
  if (clearFirst) clearIdentity();
  const epoch = sessionEpoch;
  try {
    const response = await apiFetch("/api/auth/me", { cache: "no-store" });
    if (epoch !== sessionEpoch || authBusy) {
      if (authBusy) pendingIdentityCheck = true;
      return;
    }
    if (response.status === 401) {
      if (currentUser) clearIdentity();
      if (hadUser || clearFirst) $("auth-status").textContent = "登录已失效，请重新登录。";
      if (hadUser && !clearFirst) broadcastSessionChange();
      return;
    }
    const payload = await responsePayload(response);
    if (epoch !== sessionEpoch || authBusy) {
      if (authBusy) pendingIdentityCheck = true;
      return;
    }
    if (currentUser?.id !== payload.user.id || csrfToken !== payload.csrf_token) {
      clearIdentity();
      currentUser = payload.user;
      csrfToken = payload.csrf_token;
      showAccount();
      await loadThreads();
      if (currentUser?.id === payload.user.id && csrfToken === payload.csrf_token)
        $("auth-status").textContent = "账号已更新，请选择或新建线程。";
    }
  } catch (error) {
    if (error.name !== "AbortError" && epoch === sessionEpoch)
      $("auth-status").textContent = "无法确认登录状态：" + error.message;
  }
}

function resumeIdentityCheck() {
  if (pendingRemoteChange) {
    pendingRemoteChange = false;
    pendingIdentityCheck = false;
    void verifyIdentity(true);
  } else if (pendingIdentityCheck) {
    pendingIdentityCheck = false;
    void verifyIdentity();
  }
}

if (authChannel) authChannel.onmessage = event => {
  if (event.data?.type === "session-changed") void verifyIdentity(true);
};
else window.addEventListener("storage", event => {
  if (event.key === authStorageKey && event.newValue) void verifyIdentity(true);
});
window.addEventListener("focus", () => void verifyIdentity());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void verifyIdentity();
});

async function loadThreads(preferredId = null) {
  const epoch = sessionEpoch;
  const threads = await responsePayload(await apiFetch("/api/threads"));
  if (epoch !== sessionEpoch) return;
  const select = $("thread-select");
  select.replaceChildren(new Option("请选择线程", ""));
  for (const thread of threads) select.add(new Option(thread.id, thread.id));
  select.value = preferredId && threads.some(thread => thread.id === preferredId) ? preferredId : "";
  selectThread(select.value);
}

function selectThread(id) {
  advanceSession();
  resetBusinessState();
  threadId = id || null;
  $("thread-select").value = id || "";
  showAccount();
  if (threadId) {
    initDomains();
    void loadDiagnosticList();
    void loadChatHistory();
  }
}

async function createThread() {
  if (!currentUser || !serviceReady) return;
  const epoch = sessionEpoch;
  const ownerId = currentUser.id;
  $("thread-create").disabled = true;
  try {
    const thread = await responsePayload(await apiFetch("/api/threads", { method: "POST" }));
    if (epoch !== sessionEpoch) return;
    await loadThreads(thread.id);
    if (currentUser?.id === ownerId) $("auth-status").textContent = "已创建线程。";
  } catch (error) {
    if (epoch === sessionEpoch && error.name !== "AbortError")
      $("auth-status").textContent = "创建线程失败：" + error.message;
  } finally {
    $("thread-create").disabled = false;
  }
}

async function authenticate(register) {
  const form = $("auth-form");
  if (!form.reportValidity()) return;
  const username = $("auth-username").value;
  const password = $("auth-password").value;
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$/.test(username)) {
    $("auth-status").textContent = "用户名须为 3–64 位，以英文字母或数字开头。";
    return;
  }
  form.querySelectorAll("input, button").forEach(control => { control.disabled = true; });
  authBusy = true;
  $("auth-status").textContent = register ? "正在注册…" : "正在登录…";
  try {
    const options = { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }) };
    if (register) await responsePayload(await apiFetch("/api/auth/register", options));
    const payload = await responsePayload(await apiFetch("/api/auth/login", options));
    advanceSession();
    resetBusinessState();
    currentUser = payload.user;
    csrfToken = payload.csrf_token;
    showAccount();
    broadcastSessionChange();
    await loadThreads();
    $("auth-password").value = "";
    $("auth-status").textContent = "登录成功，请选择或新建线程。";
  } catch (error) {
    if (error.name !== "AbortError") $("auth-status").textContent = "登录失败：" + error.message;
  } finally {
    authBusy = false;
    showAccount();
    resumeIdentityCheck();
  }
}

async function logout() {
  $("auth-logout").disabled = true;
  authBusy = true;
  advanceSession();
  resetRequestTrace();
  try {
    const response = await apiFetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    if (error.name !== "AbortError") $("auth-status").textContent = "退出失败：" + error.message;
    $("auth-logout").disabled = false;
    authBusy = false;
    resumeIdentityCheck();
    return;
  }
  advanceSession();
  currentUser = null;
  csrfToken = null;
  resetBusinessState();
  $("thread-select").replaceChildren();
  $("auth-status").textContent = "已退出登录。";
  showAccount();
  broadcastSessionChange();
  $("auth-logout").disabled = false;
  authBusy = false;
  resumeIdentityCheck();
}

function setApplicationEnabled(enabled) {
  document.querySelectorAll(
    "header button, main button, main input, main select, main textarea",
  ).forEach(control => { control.disabled = !enabled; });
  if (enabled) {
    updateDiagnosticListAction();
    if (chatHistoryLoading) $("chat-send").disabled = true;
  }
}

function readinessMessage(payload) {
  return "系统尚未就绪，请稍后重新检查。";
}

async function checkServiceReadiness() {
  const banner = $("service-status");
  const message = $("service-status-message");
  setApplicationEnabled(false);
  try {
    const response = await apiFetch("/api/ready", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      serviceReady = false;
      message.textContent = readinessMessage(payload);
      banner.classList.remove("hidden");
      showAccount();
      return false;
    }
    serviceReady = true;
    banner.classList.add("hidden");
    showAccount();
    return true;
  } catch (error) {
    serviceReady = false;
    message.textContent = "无法确认服务状态：" + error.message;
    banner.classList.remove("hidden");
    showAccount();
    return false;
  }
}

async function bootstrap() {
  if (!await checkServiceReadiness()) return;
  const hadUser = !!currentUser;
  try {
    const response = await apiFetch("/api/auth/me");
    const payload = await responsePayload(response);
    advanceSession();
    resetBusinessState();
    currentUser = payload.user;
    csrfToken = payload.csrf_token;
    showAccount();
    await loadThreads();
  } catch (error) {
    if (error.name !== "AbortError") {
      advanceSession();
      resetBusinessState();
      currentUser = null;
      csrfToken = null;
      if (hadUser) $("auth-status").textContent = "登录已失效，请重新登录。";
      showAccount();
    }
  }
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
let requestTrace = null;
let traceGeneration = 0;
let traceController = null;

function resetRequestTrace() {
  traceController?.abort();
  traceController = null;
  traceGeneration += 1;
  requestTrace = null;
  $("request-trace").open = false;
  $("request-trace").classList.add("hidden");
  $("request-trace-status").textContent = "";
  $("request-trace-content").replaceChildren();
}

function showRequestTrace(event = {}) {
  if (!requestTrace) return;
  if (event.request_id) requestTrace.id = event.request_id;
  if (typeof event.trace_persisted === "boolean") requestTrace.persisted = event.trace_persisted;
  if (event.conversation_commit) requestTrace.commit = event.conversation_commit;
  const { id, persisted, commit } = requestTrace;
  $("request-trace").classList.remove("hidden");
  $("request-trace-status").textContent = `请求 ID：${id || "未知"}\n`
    + (persisted === false ? "本轮 trace 未确认持久化，不能作为可恢复记录。"
      : persisted === true ? "服务端报告 trace 已持久化；展开或刷新详情读取当前账号的记录。"
        : "持久化状态未知；请求 ID 本身不证明记录已保存。")
    + `\nSQL conversation_commit：${commit || "未知"}（仅表示对话 SQL 提交观察）`;
  $("request-trace-refresh").disabled = !id || !!traceController;
}

function renderRequestTrace(record) {
  const content = $("request-trace-content");
  content.replaceChildren();
  const line = text => {
    const p = document.createElement("p");
    p.textContent = text;
    content.appendChild(p);
  };
  const known = value => value === null || value === undefined ? "未知" : String(value);
  const tokens = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : "未知";
  const trace = record.trace || {};
  if (requestTrace?.persisted === false)
    line("本轮终态回执未确认持久化；下面可能是此前保存的观察快照，不能证明本轮终态已保存。");
  line(`持久记录状态：${known(record.status)}；SQL conversation_commit：${known(trace.conversation_commit)}`);
  line("阶段 / 从请求开始经过时间（不是各阶段独立耗时）：");
  for (const event of trace.events || [])
    line(`${known(event.stage)} · ${known(event.outcome)} · ${known(event.elapsed_ms)} ms`);
  for (const [label, calls] of [["模型", trace.model_calls], ["服务", trace.service_calls]]) {
    line(`${label}调用回执：`);
    for (const call of calls || []) {
      const usage = call.usage || {};
      const duration = typeof call.ended_ms === "number" && typeof call.started_ms === "number"
        ? Math.round((call.ended_ms - call.started_ms) * 10) / 10 : null;
      line(`${label} · ${known(call.kind || call.stage)} · ${known(call.status)} · 耗时 ${known(duration)} ms\n`
        + `请求 model：${known(call.requested_model)}；回执 model：${known(call.response_model)}；`
        + `响应已观察：${call.response_observed === true ? "是" : "未确认"}\n`
        + `prompt_tokens：${tokens(usage.prompt_tokens)}；completion_tokens：${tokens(usage.completion_tokens)}`
        + (label === "服务" ? `；total_tokens（此服务回执）：${tokens(usage.total_tokens)}` : ""));
    }
  }
  line("token 仅为各次已观察回执，不合计为全请求 total；不覆盖 SDK 内部重试或未观察调用。");
  line("费用：未知（尚未绑定价格；回执不是供应商账单）。");
  if (trace.dropped_events || trace.dropped_model_calls || trace.dropped_service_calls)
    line(`记录达到上限：省略阶段 ${known(trace.dropped_events)}、模型调用 ${known(trace.dropped_model_calls)}、服务调用 ${known(trace.dropped_service_calls)}。`);
  line(`恢复观察：${known(record.recovery?.state)}；原因：${known(record.recovery?.reason)}`);
  line(record.recovery?.state === "unconfirmed"
    ? "unconfirmed：无法证明执行已停止、已回滚或可以安全重发。请先刷新历史核对；此处不会自动重放。"
    : "这是观察记录，不能证明执行已停止、已回滚或可以安全重发；请先核对历史，此处不会自动重放。");
}

async function refreshRequestTrace() {
  if (!requestTrace?.id) return;
  traceController?.abort();
  const controller = new AbortController();
  traceController = controller;
  const generation = ++traceGeneration;
  const epoch = sessionEpoch;
  const selectedThread = threadId;
  const id = requestTrace.id;
  const current = () => generation === traceGeneration && epoch === sessionEpoch
    && selectedThread === threadId && requestTrace?.id === id;
  $("request-trace-refresh").disabled = true;
  $("request-trace-content").textContent = "正在读取持久记录…";
  try {
    const response = await apiFetch(`/api/traces/${encodeURIComponent(id)}`, {
      cache: "no-store", signal: controller.signal,
    });
    if (!current()) return;
    if (response.status === 404) {
      $("request-trace-content").textContent = "当前账号没有可读取的持久记录（404）。不能据此证明请求未执行、已回滚或可安全重发；请先核对历史。";
      return;
    }
    const record = await responsePayload(response);
    if (!current()) return;
    if (record.request_id !== id || record.thread_id !== selectedThread)
      throw new Error("记录与当前请求或线程不匹配");
    renderRequestTrace(record);
  } catch (error) {
    if (current() && error.name !== "AbortError")
      $("request-trace-content").textContent = "详情读取失败：" + error.message + "。持久记录未确认，请先核对历史。";
  } finally {
    if (current()) {
      traceController = null;
      $("request-trace-refresh").disabled = false;
    }
  }
}
$("request-trace-refresh").onclick = refreshRequestTrace;
$("request-trace").ontoggle = () => {
  if ($("request-trace").open && requestTrace?.id && !$("request-trace-content").textContent)
    void refreshRequestTrace();
};

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

function makeSourceLink(source, page, locator = null, sourceUrl = null) {
  const link = document.createElement("a");
  link.className = "source-link";
  link.href = documentUrl(source, page);
  link.target = "_blank";
  link.rel = "noopener";
  if (page == null && locator) {
    link.textContent = `《${source}》定位【${locator}】 ↗`;
    link.title = "打开结构化原始资料，按控制编号定位";
    try {
      const url = new URL(sourceUrl);
      if (url.protocol === "https:" && !url.username && !url.password) link.href = url.href;
      else link.removeAttribute("href");
    } catch { link.removeAttribute("href"); }
    return link;
  }
  link.textContent = `《${source}》第${page}页 ↗`;
  link.title = "在新标签页打开原始课件并定位到此页";
  return link;
}

async function sendChat() {
  if (!threadId || chatHistoryLoading || $("chat-send").disabled) return;
  const epoch = sessionEpoch;
  const input = $("chat-input");
  const q = input.value.trim();
  if (!q) return;
  resetRequestTrace();
  requestTrace = { id: null, persisted: null, commit: null };
  input.value = "";
  addBubble("user", q);
  const bot = addBubble("bot", "");
  $("chat-send").disabled = true;

  const sources = [];
  let completed = false;
  try {
    const resp = await apiFetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, thread_id: threadId }),
    });
    showRequestTrace({ request_id: resp.headers.get("X-Request-ID") });
    if (!resp.ok) {
      if (resp.headers.get("Content-Type")?.includes("application/json")) {
        const payload = await resp.json();
        if (epoch !== sessionEpoch) return;
        showRequestTrace(payload);
      }
      throw new Error(`HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let full = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (epoch !== sessionEpoch) return;
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
        else if (ev.type === "progress" && !full) {
          bot.textContent = ev.content;
        }
        else if (ev.type === "contexts") {
          ev.contexts.forEach(c => sources.push(c));
        }
        else if (ev.type === "trace") showRequestTrace(ev);
        else if (ev.type === "error") {
          showRequestTrace(ev);
          throw new Error(ev.message);
        }
        else if (ev.type === "done") completed = true;
        $("chat-history").scrollTop = $("chat-history").scrollHeight;
      }
    }
    if (!completed) throw new Error("本轮未确认保存，请刷新历史后再试。");
    bot.innerHTML = renderMarkdown(full.replace(/\n?【出处】[^\n]*$/, ""));
  } catch (e) {
    if (epoch !== sessionEpoch) return;
    bot.textContent += "\n[连接出错: " + e.message + "]";
  }
  if (epoch !== sessionEpoch) return;
  if (sources.length) {
    const ctx = document.createElement("div");
    ctx.className = "ctx";
    ctx.append("【出处】");
    const uniqueSources = [...new Map(sources.map(c => [`${c.source}:${c.page ?? c.locator}`, c])).values()];
    uniqueSources.slice(0, 4).forEach((c, index) => {
      if (index) ctx.append("；");
      ctx.appendChild(makeSourceLink(c.source, c.page, c.locator, c.source_url));
    });
    bot.appendChild(ctx);
  }
  $("chat-send").disabled = false;
  $("chat-input").focus();
}

async function loadChatHistory() {
  const epoch = sessionEpoch;
  const selectedThread = threadId;
  chatHistoryLoading = true;
  $("chat-send").disabled = true;
  try {
    const rows = await responsePayload(await apiFetch(
      `/api/history?thread_id=${encodeURIComponent(selectedThread)}&limit=50`, { cache: "no-store" },
    ));
    if (epoch !== sessionEpoch || selectedThread !== threadId) return;
    $("chat-history").replaceChildren();
    for (const row of rows) addBubble(row.role === "user" ? "user" : "bot", row.content);
  } catch (error) {
    if (epoch !== sessionEpoch) return;
    addBubble("bot", "读取历史失败：" + error.message + "。可重新选择线程重试。");
  } finally {
    if (epoch === sessionEpoch && selectedThread === threadId) {
      chatHistoryLoading = false;
      $("chat-send").disabled = !serviceReady || !currentUser || !threadId;
    }
  }
}
$("chat-send").onclick = sendChat;
$("chat-input").addEventListener("keydown", e => { if (e.key === "Enter") sendChat(); });

/* ══════════ 刷题 ══════════ */
let currentQuestion = null;
let answered = false;
let quizSubmission = null;
let diagnosticSessionId = null;
let diagnosticMode = false;
let lastDiagnosticReport = null;
let diagnosticStart = null;
let diagnosticSubmission = null;
let diagnosticEpoch = 0;
let diagnosticController = new AbortController();
let diagnosticListItems = [];
let diagnosticListOffset = 0;
let diagnosticListHasMore = false;
let diagnosticListLoading = false;
let diagnosticListEpoch = 0;
let diagnosticListController = new AbortController();

function resetDiagnosticList() {
  diagnosticListController.abort();
  diagnosticListController = new AbortController();
  diagnosticListEpoch += 1;
  diagnosticListItems = [];
  diagnosticListOffset = 0;
  diagnosticListHasMore = false;
  diagnosticListLoading = false;
  $("diagnostic-list-status").textContent = "";
  $("diagnostic-list-retry").classList.add("hidden");
  renderDiagnosticList();
}

function renderDiagnosticList() {
  const select = $("diagnostic-list");
  const selectedId = select.value;
  select.replaceChildren(new Option("请选择诊断", ""));
  for (const item of diagnosticListItems) {
    const domains = (item.selected_domains || []).join("、") || "未标注方向";
    const progress = item.progress || {};
    const status = item.status === "completed" ? "已完成" : "进行中";
    const createdAt = new Date(item.created_at * 1000).toLocaleString();
    const label = `${status} · ${progress.answered ?? 0}/${progress.max_questions ?? "?"} 题 · ${domains} · ${createdAt}`;
    select.add(new Option(label, item.session_id));
  }
  select.value = diagnosticListItems.some(item => item.session_id === selectedId) ? selectedId : "";
  updateDiagnosticListAction();
  $("diagnostic-list-more").classList.toggle("hidden", !diagnosticListHasMore);
  $("diagnostic-list-more").disabled = diagnosticListLoading;
}

function updateDiagnosticListAction() {
  const item = diagnosticListItems.find(row => row.session_id === $("diagnostic-list").value);
  $("diagnostic-open").textContent = item?.status === "completed" ? "查看报告" : "继续诊断";
  $("diagnostic-open").disabled = !item || !threadId || !currentUser || !serviceReady;
}

async function loadDiagnosticList(reset = false) {
  if (!threadId || !currentUser) return;
  if (reset) resetDiagnosticList();
  if (diagnosticListLoading) return;
  const epoch = sessionEpoch;
  const listEpoch = diagnosticListEpoch;
  const selectedThread = threadId;
  const offset = diagnosticListOffset;
  diagnosticListLoading = true;
  $("diagnostic-list-retry").classList.add("hidden");
  $("diagnostic-list-status").textContent = "正在读取诊断记录…";
  renderDiagnosticList();
  try {
    const payload = await responsePayload(await apiFetch(
      `/api/diagnostics?thread_id=${encodeURIComponent(selectedThread)}&offset=${offset}&limit=20`,
      { cache: "no-store", signal: diagnosticListController.signal },
    ));
    if (epoch !== sessionEpoch || listEpoch !== diagnosticListEpoch || threadId !== selectedThread) return;
    const seen = new Set(diagnosticListItems.map(item => item.session_id));
    for (const item of payload.items) {
      if (!seen.has(item.session_id)) {
        diagnosticListItems.push(item);
        seen.add(item.session_id);
      }
    }
    diagnosticListOffset = payload.next_offset ?? offset + payload.items.length;
    diagnosticListHasMore = payload.has_more;
    $("diagnostic-list-status").textContent = diagnosticListItems.length
      ? `已载入 ${diagnosticListItems.length} 条诊断记录。` : "本线程暂无诊断记录。";
  } catch (error) {
    if (epoch !== sessionEpoch || listEpoch !== diagnosticListEpoch || threadId !== selectedThread) return;
    $("diagnostic-list-status").textContent = "读取诊断记录失败：" + error.message;
    $("diagnostic-list-retry").classList.remove("hidden");
  } finally {
    if (epoch === sessionEpoch && listEpoch === diagnosticListEpoch && threadId === selectedThread) {
      diagnosticListLoading = false;
      renderDiagnosticList();
    }
  }
}

async function openDiagnosticListItem() {
  const item = diagnosticListItems.find(row => row.session_id === $("diagnostic-list").value);
  if (!item || !diagnosticMode || !threadId) return;
  resetDiagnosticRequests();
  const epoch = sessionEpoch;
  const requestEpoch = diagnosticEpoch;
  const sessionId = item.session_id;
  diagnosticSessionId = sessionId;
  currentQuestion = null;
  quizSubmission = null;
  answered = false;
  lastDiagnosticReport = null;
  $("quiz-question").classList.add("hidden");
  $("q-feedback").classList.add("hidden");
  $("diagnostic-chat").classList.add("hidden");
  $("diagnostic-open").disabled = true;
  if (item.status !== "completed") {
    await nextDiagnosticQuestion();
  } else {
    $("diagnostic-hint").textContent = "正在读取诊断报告…";
    try {
      const report = await responsePayload(await apiFetch(
        `/diagnostic/${encodeURIComponent(sessionId)}/report`,
        { cache: "no-store", signal: diagnosticController.signal },
      ));
      if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode
          || diagnosticSessionId !== sessionId) return;
      renderDiagnosticReport(report);
      $("diagnostic-hint").textContent = "已显示诊断报告。";
    } catch (error) {
      if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode) return;
      $("diagnostic-hint").textContent = "读取诊断报告失败：" + error.message + "。可再次点击查看报告。";
    }
  }
  if (epoch === sessionEpoch && diagnosticMode && $("diagnostic-list").value === sessionId)
    updateDiagnosticListAction();
}

function resetDiagnosticRequests() {
  diagnosticController.abort();
  diagnosticController = new AbortController();
  diagnosticEpoch += 1;
  diagnosticStart = null;
  diagnosticSubmission = null;
  $("diagnostic-start").disabled = false;
  $("diagnostic-start").textContent = "开始诊断";
  $("diagnostic-next").classList.add("hidden");
  $("diagnostic-next").disabled = false;
}

async function initDomains() {
  const epoch = sessionEpoch;
  try {
    const domains = await (await apiFetch("/api/domains")).json();
    if (epoch !== sessionEpoch) return;
    $("quiz-domain").replaceChildren(new Option("全部", ""));
    $("diagnostic-domains").replaceChildren();
    for (const d of domains) {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      $("quiz-domain").appendChild(opt);
      $("diagnostic-domains").appendChild(opt.cloneNode(true));
    }
  } catch (e) { /* 忽略 */ }
}

function setDiagnosticMode(enabled) {
  const changed = diagnosticMode !== enabled;
  if (changed) {
    resetDiagnosticRequests();
    resetDiagnosticList();
    currentQuestion = null;
    quizSubmission = null;
    lastDiagnosticReport = null;
    answered = false;
    $("quiz-question").classList.add("hidden");
    $("q-feedback").classList.add("hidden");
  }
  diagnosticMode = enabled;
  $("diagnostic-form").classList.toggle("hidden", !enabled);
  $("quiz-next").classList.toggle("hidden", enabled);
  $("diagnostic-chat").classList.toggle("hidden", !enabled || !lastDiagnosticReport);
  if (!enabled) diagnosticSessionId = null;
  if (changed && enabled) void loadDiagnosticList();
}

function diagnosticStartBody() {
  const selected = [...$("diagnostic-domains").selectedOptions].map(o => o.value);
  return {
    thread_id: threadId,
    selected_domains: selected,
    self_report: {
      goal: $("diagnostic-goal").value.trim(),
      daily_minutes: Number($("diagnostic-minutes").value) || 30,
      experience: $("diagnostic-experience").value.trim(),
      domain_status: Object.fromEntries(selected.map(d => [d, "自评薄弱"])),
    },
  };
}

function updateDiagnosticStartLabel() {
  if (!diagnosticStart) return;
  const changed = JSON.stringify(diagnosticStartBody()) !== diagnosticStart.body;
  $("diagnostic-start").textContent = changed
    ? "开始新诊断（放弃上次请求）" : "重试开始诊断";
  if (changed) $("diagnostic-hint").textContent =
    "配置已变更；点击开始新诊断会放弃上次待重试请求。";
}

async function startDiagnostic() {
  if (!threadId) return;
  const epoch = sessionEpoch;
  const body = diagnosticStartBody();
  if (!body.selected_domains.length) {
    $("diagnostic-hint").textContent = "至少选择一个自评薄弱方向。";
    return;
  }
  const serialized = JSON.stringify(body);
  const abandoning = !!diagnosticStart && diagnosticStart.body !== serialized;
  if (!diagnosticStart || abandoning) {
    resetDiagnosticRequests();
    diagnosticSessionId = null;
    currentQuestion = null;
    lastDiagnosticReport = null;
    $("quiz-question").classList.add("hidden");
    $("q-feedback").classList.add("hidden");
    $("diagnostic-chat").classList.add("hidden");
    diagnosticStart = {
      body: serialized,
      payload: { ...body, start_id: crypto.randomUUID() },
    };
  }
  const requestEpoch = diagnosticEpoch;
  const request = diagnosticStart;
  $("diagnostic-start").disabled = true;
  $("diagnostic-hint").textContent = abandoning
    ? "已放弃上次请求，正在开始新诊断…" : "正在建立诊断会话…";
  try {
    const resp = await apiFetch("/diagnostic/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request.payload), signal: diagnosticController.signal,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode) return;
    diagnosticStart = null;
    $("diagnostic-start").textContent = "开始诊断";
    $("diagnostic-start").disabled = false;
    diagnosticSessionId = payload.session_id;
    void loadDiagnosticList(true);
    void nextDiagnosticQuestion();
  } catch (e) {
    if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode) return;
    $("diagnostic-hint").textContent = "诊断启动失败: " + e.message
      + "。可用相同配置重试；修改配置后会开始新诊断。";
    updateDiagnosticStartLabel();
  } finally {
    if (epoch === sessionEpoch && requestEpoch === diagnosticEpoch)
      $("diagnostic-start").disabled = false;
  }
}

async function nextDiagnosticQuestion() {
  if (!diagnosticSessionId) return;
  $("diagnostic-next").classList.add("hidden");
  $("diagnostic-next").disabled = true;
  diagnosticController.abort();
  diagnosticController = new AbortController();
  diagnosticEpoch += 1;
  const epoch = sessionEpoch;
  const requestEpoch = diagnosticEpoch;
  const sessionId = diagnosticSessionId;
  currentQuestion = null;
  quizSubmission = null;
  diagnosticSubmission = null;
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "诊断选题中…";
  $("quiz-question").classList.remove("hidden");
  answered = false;
  try {
    const resp = await apiFetch(`/diagnostic/${sessionId}/next`, {
      method: "POST", signal: diagnosticController.signal,
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode
        || diagnosticSessionId !== sessionId) return;
    $("diagnostic-next").disabled = false;
    if (payload.status === "completed") {
      renderDiagnosticReport(payload.report);
      void loadDiagnosticList(true);
      return;
    }
    $("diagnostic-reason").value = "";
    renderQuestion(payload.question);
    $("diagnostic-hint").textContent =
      `已完成 ${payload.progress.answered}/${payload.progress.max_questions} 题；会插入校准题。`;
  } catch (e) {
    if (epoch !== sessionEpoch || requestEpoch !== diagnosticEpoch || !diagnosticMode) return;
    $("q-stem").textContent = "诊断出题失败: " + e.message;
    $("diagnostic-next").disabled = false;
    $("diagnostic-next").classList.remove("hidden");
  }
}

function renderDiagnosticReport(report) {
  lastDiagnosticReport = report;
  diagnosticSubmission = null;
  $("diagnostic-next").classList.add("hidden");
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
  if (!threadId) return;
  const epoch = sessionEpoch;
  if (diagnosticMode) return nextDiagnosticQuestion();
  const mode = $("quiz-mode").value;
  const domain = $("quiz-domain").value || null;
  currentQuestion = null;
  quizSubmission = null;
  $("quiz-next").disabled = true;
  $("quiz-question").classList.remove("hidden");
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "加载中…";
  answered = false;
  try {
    const resp = await apiFetch("/quiz/next", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, domain, thread_id: threadId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (epoch !== sessionEpoch) return;
    renderQuestion(payload);
  } catch (e) {
    if (epoch !== sessionEpoch) return;
    $("q-stem").textContent = "出题失败: " + e.message;
  }
  if (epoch !== sessionEpoch) return;
  $("quiz-next").disabled = false;
}

async function choose(letter, el) {
  if (answered || !currentQuestion) return;
  const epoch = sessionEpoch;
  const requestEpoch = diagnosticEpoch;
  const question = currentQuestion;
  const isDiagnostic = diagnosticMode;
  const sessionId = diagnosticSessionId;
  let submission = isDiagnostic ? diagnosticSubmission : quizSubmission;
  if (isDiagnostic) {
    if (!sessionId || (submission && (submission.question !== question
        || submission.sessionId !== sessionId || submission.payload.choice !== letter))) return;
    if (!submission) {
      submission = {
        question, sessionId,
        payload: { question_id: question.id, choice: letter,
          confidence: Number($("diagnostic-confidence").value),
          reason: $("diagnostic-reason").value.trim() },
      };
      diagnosticSubmission = submission;
    }
  } else {
    if (!submission || (submission.payload && submission.payload.choice !== letter)) return;
    submission.payload ||= {
      question_id: question.id, choice: letter, thread_id: threadId,
      submission_id: submission.id,
    };
  }
  answered = true;
  document.querySelectorAll(".option").forEach(o => o.classList.add("locked"));
  el.classList.add("chosen");
  let result;
  try {
    const resp = await apiFetch(isDiagnostic
      ? `/diagnostic/${sessionId}/answer`
      : "/quiz/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(submission.payload),
      ...(isDiagnostic ? { signal: diagnosticController.signal } : {}),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (epoch !== sessionEpoch || currentQuestion !== question || diagnosticMode !== isDiagnostic
        || (isDiagnostic ? requestEpoch !== diagnosticEpoch
          || diagnosticSubmission !== submission || diagnosticSessionId !== sessionId
          : quizSubmission !== submission)) return;
    result = isDiagnostic ? { ...payload, ...(payload.result || {}) } : payload;
  } catch (e) {
    if (epoch !== sessionEpoch || currentQuestion !== question || diagnosticMode !== isDiagnostic
        || (isDiagnostic ? requestEpoch !== diagnosticEpoch
          || diagnosticSubmission !== submission || diagnosticSessionId !== sessionId
          : quizSubmission !== submission)) return;
    $("q-stem").textContent = question.stem + "\n[判分失败: " + e.message + "]";
    answered = false;
    el.classList.remove("locked");
    $("q-stem").textContent += isDiagnostic
      ? "\n请点击原选项重试。" : "\n请点击原选项重试，或出一题开始新的练习。";
    return;
  }
  $("q-stem").textContent = question.stem;
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
    void loadDiagnosticList(true);
  } else if (diagnosticMode) {
    setTimeout(() => {
      if (epoch === sessionEpoch && requestEpoch === diagnosticEpoch
          && diagnosticSessionId === sessionId && currentQuestion === question)
        nextDiagnosticQuestion();
    }, 250);
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
  const epoch = sessionEpoch;
  const question = currentQuestion;
  currentQuestion = null;
  quizSubmission = null;
  $("quiz-next").disabled = true;
  $("quiz-question").classList.remove("hidden");
  $("q-feedback").classList.add("hidden");
  $("q-options").innerHTML = "";
  $("q-stem").textContent = "正在围绕同一考点出新题（约需十几秒）…";
  answered = false;
  try {
    const resp = await apiFetch("/quiz/next", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "weak", domain: question.domain,
                             anchor_question_id: question.id,
                             thread_id: threadId }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const payload = await resp.json();
    if (epoch !== sessionEpoch) return;
    renderQuestion(payload);
  } catch (e) {
    if (epoch !== sessionEpoch) return;
    $("q-stem").textContent = "出题失败: " + e.message;
  }
  if (epoch !== sessionEpoch) return;
  $("quiz-next").disabled = false;
}

function renderQuestion(q) {
  currentQuestion = q.question || q;
  quizSubmission = diagnosticMode ? null : { id: crypto.randomUUID(), payload: null };
  diagnosticSubmission = null;
  answered = false;
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
    div.onclick = () => { if (currentQuestion === question) choose(letter, div); };
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
    const epoch = sessionEpoch;
    activeKnowledgePointsPromise = apiFetch("/api/knowledge-points?status=active")
      .then(async response => {
        if (!response.ok) throw new Error("无法读取已启用知识点");
        return response.json();
      })
      .catch(error => {
        if (epoch === sessionEpoch) activeKnowledgePointsPromise = null;
        throw error;
      });
  }
  return activeKnowledgePointsPromise;
}

async function showCorrectionForm(cell, item) {
  const epoch = sessionEpoch;
  cell.textContent = "正在加载…";
  try {
    const points = await activeKnowledgePoints();
    if (epoch !== sessionEpoch) return;
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
        const response = await apiFetch(
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
        if (epoch !== sessionEpoch) return;
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
        if (epoch !== sessionEpoch) return;
        renderAttributionReviewRows();
        $("stats-summary").textContent = successMessage + "；" + $("stats-summary").textContent;
      } catch (error) {
        if (epoch !== sessionEpoch) return;
        status.textContent = "纠错失败：" + error.message;
        save.disabled = false;
      }
    };
    cancel.onclick = () => renderAttributionReviewRows();
    form.append(search, matchCount, select, reason, save, cancel, status);
    cell.replaceChildren(form);
  } catch (error) {
    if (epoch !== sessionEpoch) return;
    cell.textContent = "无法纠错：" + error.message;
  }
}

async function loadAttributionObservability() {
  if (!threadId) return;
  const epoch = sessionEpoch;
  const panel = $("attribution-observability");
  const summary = $("attribution-observability-summary");
  const domains = $("attribution-observability-domains");
  try {
    const response = await apiFetch(
      `/api/attribution-observability?thread_id=${encodeURIComponent(threadId)}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (epoch !== sessionEpoch) return;
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
    if (epoch !== sessionEpoch) return;
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
    const legacyCount = Number(item.legacy_applied_attempts_without_point || 0);
    correctionButton.textContent = legacyCount
      ? "需人工核对" : item.source === "user_correction" ? "再次纠正" : "纠正";
    correctionButton.disabled = legacyCount > 0;
    if (!legacyCount) correctionButton.onclick = () => showCorrectionForm(action, item);
    action.appendChild(correctionButton);
    if (legacyCount) {
      const note = document.createElement("small");
      note.className = "attribution-legacy-note";
      note.textContent = `历史已入画像作答 ${legacyCount} 条缺精确归因，不能直接纠错`;
      action.appendChild(note);
    }
    tr.appendChild(action);
    body.appendChild(tr);
  }
}

async function loadAttributionReviewPage({reset = false} = {}) {
  if (!threadId) return;
  const epoch = sessionEpoch;
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
    const response = await apiFetch(`/api/attribution-review?${params}`, {cache: "no-store"});
    const page = await response.json();
    if (epoch !== sessionEpoch) return;
    if (!response.ok) throw new Error(page.detail || "服务暂不可用");
    attributionReviewState.items = reset
      ? page.items : attributionReviewState.items.concat(page.items);
    attributionReviewState.cursor = page.next_cursor;
    attributionReviewState.hasMore = page.has_more;
    attributionReviewState.total = page.total;
  } catch (error) {
    if (epoch !== sessionEpoch) return;
    attributionReviewState.error = error.message;
  } finally {
    if (epoch !== sessionEpoch) return;
    attributionReviewState.loading = false;
    renderAttributionReviewRows();
  }
}

async function loadStats({refreshAttributionReview = true} = {}) {
  if (!threadId) return;
  const epoch = sessionEpoch;
  try {
    const s = await (await apiFetch(`/api/stats?thread_id=${encodeURIComponent(threadId)}`)).json();
    if (epoch !== sessionEpoch) return;
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
    const legacyCount = Number(s.legacy_applied_attempts_without_point || 0);
    if (legacyCount) {
      $("stats-summary").textContent +=
        `；历史已入画像作答 ${legacyCount} 条缺精确归因，相关题目需人工核对`;
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
    if (epoch !== sessionEpoch) return;
    await loadAttributionObservability();
  } catch (e) { /* 忽略 */ }
}

$("quiz-mode").onchange = () => setDiagnosticMode($("quiz-mode").value === "diagnostic");
$("diagnostic-start").onclick = startDiagnostic;
$("diagnostic-next").onclick = nextDiagnosticQuestion;
$("diagnostic-list").onchange = () => {
  resetDiagnosticRequests();
  diagnosticSessionId = null;
  currentQuestion = null;
  diagnosticSubmission = null;
  lastDiagnosticReport = null;
  $("quiz-question").classList.add("hidden");
  $("q-feedback").classList.add("hidden");
  $("diagnostic-chat").classList.add("hidden");
  updateDiagnosticListAction();
};
$("diagnostic-open").onclick = openDiagnosticListItem;
$("diagnostic-list-retry").onclick = () => loadDiagnosticList();
$("diagnostic-list-more").onclick = () => loadDiagnosticList();
for (const id of ["diagnostic-domains", "diagnostic-goal", "diagnostic-minutes",
                  "diagnostic-experience"]) {
  $(id).addEventListener("input", updateDiagnosticStartLabel);
  $(id).addEventListener("change", updateDiagnosticStartLabel);
}
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
$("auth-form").onsubmit = event => { event.preventDefault(); authenticate(false); };
$("auth-register").onclick = () => authenticate(true);
$("auth-logout").onclick = logout;
$("thread-create").onclick = createThread;
$("thread-select").onchange = event => selectThread(event.target.value);
$("service-retry").onclick = bootstrap;
bootstrap();
