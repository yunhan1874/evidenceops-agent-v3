const state = {
  sessions: [],
  activeSession: null,
  activeRun: null,
  activeTab: "evidence",
  pendingScenario: null,
  loading: false,
  demoData: false,
};

const scenarios = {
  grounded: {
    query: "公司的采购审批流程是什么？",
  },
  noEvidence: {
    query: "采购制度是什么？【演练：无证据】",
  },
  injection: {
    query: "采购制度是什么？【演练：提示注入】",
  },
  vagueFeedback: {
    query: "知识库有问题，请提交反馈",
  },
  feedback: {
    query: "食品添加剂标准里山梨酸钾最大用量说明不准确，请提交知识库反馈。",
  },
  compare: {
    query: "比较《2025版采购制度》和《2026版采购制度》的差异。【演练：双来源】",
    document_a: "2025版采购制度",
    document_b: "2026版采购制度",
  },
};

const elements = {
  devUser: document.querySelector("#dev-user"),
  refreshButton: document.querySelector("#refresh-button"),
  sampleButton: document.querySelector("#sample-button"),
  newSessionButton: document.querySelector("#new-session-button"),
  deleteSessionButton: document.querySelector("#delete-session-button"),
  sessionList: document.querySelector("#session-list"),
  activeSessionTitle: document.querySelector("#active-session-title"),
  activeSessionMeta: document.querySelector("#active-session-meta"),
  activeStatus: document.querySelector("#active-status"),
  messageStream: document.querySelector("#message-stream"),
  approvalCard: document.querySelector("#approval-card"),
  approvalDraft: document.querySelector("#approval-draft"),
  approvalComment: document.querySelector("#approval-comment"),
  approveButton: document.querySelector("#approve-button"),
  rejectButton: document.querySelector("#reject-button"),
  composer: document.querySelector("#composer"),
  queryInput: document.querySelector("#query-input"),
  sendButton: document.querySelector("#send-button"),
  inspectorContent: document.querySelector("#inspector-content"),
  inspectorRunStatus: document.querySelector("#inspector-run-status"),
  apiOrigin: document.querySelector("#api-origin"),
  toastStack: document.querySelector("#toast-stack"),
};

function newId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function className(value) {
  return String(value || "neutral").replace(/[^a-zA-Z0-9_-]/g, "");
}

function apiHeaders(extra = {}) {
  return {
    "Content-Type": "application/json",
    "X-Dev-User": elements.devUser.value.trim() || "github-demo",
    "X-Request-ID": `ui-${newId()}`,
    ...extra,
  };
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: apiHeaders(options.headers || {}),
  });
  const requestId = response.headers.get("X-Request-ID");
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : body.detail?.message || JSON.stringify(body.detail);
    } catch (_) {
      // Keep the HTTP fallback message.
    }
    throw new Error(requestId ? `${detail} · Request-ID ${requestId}` : detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, type = "info") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  elements.toastStack.append(item);
  setTimeout(() => item.remove(), 4200);
}

function setStatus(element, label, status = "neutral") {
  element.textContent = label;
  element.className = `status-pill ${className(status)}`;
}

function formatDate(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function setLoading(loading) {
  state.loading = loading;
  elements.sendButton.disabled = loading;
  elements.queryInput.disabled = loading;
  elements.approveButton.disabled = loading;
  elements.rejectButton.disabled = loading;
}

function resizeComposer() {
  elements.queryInput.style.height = "auto";
  elements.queryInput.style.height = `${Math.min(elements.queryInput.scrollHeight, 140)}px`;
}

function renderSessions() {
  if (!state.sessions.length) {
    elements.sessionList.innerHTML = `
      <div class="empty-list">
        <span class="empty-icon">◎</span>
        <p>还没有会话。点击右上角的加号创建一个。</p>
      </div>`;
    return;
  }

  elements.sessionList.innerHTML = state.sessions
    .map(
      (session) => `
        <button class="session-item ${state.activeSession?.id === session.id ? "active" : ""}"
          data-session-id="${escapeHtml(session.id)}" type="button">
          <span class="session-glyph">${session.demo ? "◇" : "◎"}</span>
          <span class="session-copy">
            <strong>${escapeHtml(session.title)}</strong>
            <span>${session.demo ? "示例数据" : formatDate(session.created_at)}</span>
          </span>
        </button>`,
    )
    .join("");

  elements.sessionList.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function welcomeMarkup() {
  return `
    <article class="welcome-card">
      <div class="welcome-badge">WHY THIS IS NOT ANOTHER CHATBOT</div>
      <h3>每个答案都要先通过证据门禁。</h3>
      <p>RAGFlow 只负责生成候选答案；Evidence Guard 根据引用、相关度、来源覆盖和安全风险决定正常回答、警告、拒答或阻断。每次判断都会留下 Evidence Passport 与 Harness 轨迹。</p>
      <div class="capability-grid">
        <div><strong>4</strong><span>Verdict 等级</span></div>
        <div><strong>5</strong><span>Tool Contracts</span></div>
        <div><strong>200</strong><span>分层评测样本</span></div>
      </div>
    </article>`;
}

function citationsMarkup(citations = []) {
  if (!citations.length) return "";
  return `<div class="citation-list">${citations
    .slice(0, 3)
    .map(
      (citation) => `
        <div class="citation-card">
          <strong>${escapeHtml(citation.document_name || "未知文档")}</strong>
          ${citation.page !== null && citation.page !== undefined ? ` · 第 ${escapeHtml(citation.page)} 页` : ""}
          ${citation.score !== null && citation.score !== undefined ? ` · score ${Number(citation.score).toFixed(2)}` : ""}
        </div>`,
    )
    .join("")}</div>`;
}

function messageMarkup(role, text, run = null) {
  const isUser = role === "user";
  const verdict = run?.evidence_passport?.verdict;
  return `
    <article class="message ${isUser ? "user" : "assistant"}" ${run?.run_id ? `data-run-id="${escapeHtml(run.run_id)}"` : ""}>
      <div class="message-avatar">${isUser ? "YOU" : "AI"}</div>
      <div class="message-bubble">
        ${escapeHtml(text)}
        ${!isUser ? citationsMarkup(run?.citations || []) : ""}
        ${!isUser && run ? `<div class="message-meta">
          ${run.intent ? `<span>${escapeHtml(run.intent)}</span>` : ""}
          ${verdict ? `<span class="status-pill ${className(verdict)}">${escapeHtml(verdict)}</span>` : ""}
          ${run.run_id ? `<span>${escapeHtml(String(run.run_id).slice(0, 8))}</span>` : ""}
        </div>` : ""}
      </div>
    </article>`;
}

function runMessagesMarkup(run) {
  return messageMarkup("user", run.query || run._query || "历史问题") + messageMarkup("assistant", run.answer || "等待人工确认。", run);
}

function renderHistory(items = []) {
  elements.messageStream.innerHTML = items.length
    ? items.map(runMessagesMarkup).join("")
    : welcomeMarkup();

  elements.messageStream.querySelectorAll("[data-run-id]").forEach((message) => {
    message.addEventListener("click", () => loadRun(message.dataset.runId));
  });
  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function appendThinking() {
  const node = document.createElement("article");
  node.id = "thinking-message";
  node.className = "message assistant";
  node.innerHTML = `
    <div class="message-avatar">AI</div>
    <div class="message-bubble thinking-bubble"><span></span><span></span><span></span></div>`;
  elements.messageStream.append(node);
  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function appendRun(query, run) {
  document.querySelector("#thinking-message")?.remove();
  const wrapper = document.createElement("div");
  wrapper.innerHTML = messageMarkup("user", query) + messageMarkup("assistant", run.answer || "等待人工确认。", run);
  while (wrapper.firstElementChild) elements.messageStream.append(wrapper.firstElementChild);
  const latest = elements.messageStream.querySelector(`[data-run-id="${CSS.escape(String(run.run_id))}"]`);
  latest?.addEventListener("click", () => loadRun(run.run_id));
  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function showApproval(run) {
  const pending = run?.pending_approval;
  if (!pending || run.status !== "pending_approval") {
    elements.approvalCard.classList.add("hidden");
    return;
  }
  const draft = pending.draft || {};
  elements.approvalDraft.innerHTML = `
    <div class="draft-field"><span>知识点</span><strong>${escapeHtml(draft.knowledge_item || "未提供")}</strong></div>
    <div class="draft-field"><span>问题类型</span><strong>${escapeHtml(draft.issue_type || "content_issue")}</strong></div>
    <div class="draft-field"><span>优先级</span><strong>${escapeHtml(draft.priority || "normal")}</strong></div>
    <div class="draft-field"><span>风险等级</span><strong>${escapeHtml(pending.risk_level || "high")}</strong></div>
    <div class="draft-field wide"><span>问题描述</span><strong>${escapeHtml(draft.description || "未提供")}</strong></div>`;
  elements.approvalCard.classList.remove("hidden");
}

function renderInspector() {
  const run = state.activeRun;
  if (!run) {
    setStatus(elements.inspectorRunStatus, "暂无 Run", "neutral");
    elements.inspectorContent.innerHTML = `
      <div class="inspector-empty">
        <div class="radar" aria-hidden="true"><span></span></div>
        <h3>等待一次 Agent 运行</h3>
        <p>运行后可在这里检查 Evidence Passport、Harness Timeline 和 Tool Calls。</p>
      </div>`;
    return;
  }

  setStatus(elements.inspectorRunStatus, run.status || "unknown", run.status || "neutral");
  if (state.activeTab === "evidence") renderEvidence(run);
  if (state.activeTab === "trace") renderTrace(run);
  if (state.activeTab === "tools") renderTools(run);
}

function renderEvidence(run) {
  const passport = run.evidence_passport;
  if (!passport) {
    elements.inspectorContent.innerHTML = `
      <div class="inspector-empty">
        <div class="radar" aria-hidden="true"><span></span></div>
        <h3>本次运行没有 Evidence Passport</h3>
        <p>${run.intent === "knowledge_feedback" ? "知识反馈分支不经过回答证据门禁。" : "该意图没有产生可核验的检索证据。"}</p>
      </div>`;
    return;
  }
  const coverage = passport.coverage || {};
  const confidence = Math.round((passport.confidence || 0) * 100);
  elements.inspectorContent.innerHTML = `
    <div class="passport-hero">
      <div class="passport-verdict-row">
        <div>
          <p class="section-kicker">EVIDENCE PASSPORT</p>
          <span class="status-pill ${className(passport.verdict)}">${escapeHtml(passport.verdict)}</span>
        </div>
        <div class="confidence-ring" style="--confidence: ${confidence}%"><span>${confidence}%</span></div>
      </div>
      <div class="mono-id">${escapeHtml(passport.policy_version)} · ${escapeHtml(passport.task)}</div>
      <div class="metric-grid">
        <div class="metric-card"><span>引用数</span><strong>${coverage.citation_count ?? 0}</strong></div>
        <div class="metric-card"><span>独立来源</span><strong>${coverage.source_count ?? 0}</strong></div>
        <div class="metric-card"><span>平均分</span><strong>${coverage.average_score ?? "—"}</strong></div>
        <div class="metric-card"><span>最高分</span><strong>${coverage.top_score ?? "—"}</strong></div>
      </div>
    </div>
    <section class="detail-section">
      <h3>风险标记</h3>
      <div class="flag-list">${passport.risk_flags?.length ? passport.risk_flags.map((flag) => `<span class="flag">${escapeHtml(flag)}</span>`).join("") : '<span class="flag">none</span>'}</div>
    </section>
    <section class="detail-section">
      <h3>判定原因</h3>
      ${(passport.reasons || []).map((reason) => `<div class="detail-card">${escapeHtml(reason)}</div>`).join("")}
    </section>
    <section class="detail-section">
      <h3>证据片段</h3>
      ${(passport.evidence || []).map((item) => `
        <div class="detail-card">
          <strong>${escapeHtml(item.document_name)}</strong> · page ${escapeHtml(item.page ?? "—")} · score ${escapeHtml(item.score ?? "—")}
          <div class="mono-id">${escapeHtml(item.evidence_id)}</div>
          <div class="evidence-snippet">${escapeHtml(item.snippet)}</div>
        </div>`).join("") || '<div class="detail-card">没有可展示的证据。</div>'}
    </section>
    <section class="detail-section">
      <h3>恢复建议</h3>
      <div class="detail-card"><strong>${escapeHtml(passport.recommended_action)}</strong><br>${escapeHtml((passport.recovery_plan || []).join(" · "))}</div>
    </section>`;
}

function renderTrace(run) {
  const harness = run.harness || {};
  const events = harness.events || [];
  elements.inspectorContent.innerHTML = `
    <div class="detail-card">
      <strong>Run</strong><div class="mono-id">${escapeHtml(run.run_id)}</div>
      <br><strong>终止原因</strong> · ${escapeHtml(harness.termination_reason || "等待运行完成")}
    </div>
    <div class="metric-grid">
      <div class="metric-card"><span>最大步骤</span><strong>${harness.budget?.max_steps ?? "—"}</strong></div>
      <div class="metric-card"><span>工具预算</span><strong>${harness.budget?.max_tool_calls ?? "—"}</strong></div>
      <div class="metric-card"><span>上下文轮数</span><strong>${harness.context?.turns_kept ?? "—"}</strong></div>
      <div class="metric-card"><span>模型路由</span><strong>${harness.model_routes?.length ?? 0}</strong></div>
    </div>
    <section class="detail-section">
      <h3>版本指纹</h3>
      <div class="detail-card mono-id">${Object.entries(harness.versions || {}).map(([key, value]) => `${escapeHtml(key)}=${escapeHtml(value)}`).join("<br>") || "无版本信息"}</div>
    </section>
    <section class="detail-section">
      <h3>Harness Timeline</h3>
      <div class="timeline">${events.map((event) => `
        <div class="timeline-item">
          <div class="timeline-head"><strong>${escapeHtml(event.name)}</strong><span class="status-pill ${className(event.status)}">${escapeHtml(event.status)}</span></div>
          <p>${escapeHtml(event.event_type)}${event.duration_ms !== null && event.duration_ms !== undefined ? ` · ${event.duration_ms} ms` : ""}</p>
        </div>`).join("") || '<div class="detail-card">没有 Harness 事件。</div>'}</div>
    </section>`;
}

function renderTools(run) {
  const calls = run.tool_calls || [];
  elements.inspectorContent.innerHTML = `
    <div class="detail-card"><strong>${calls.length}</strong> 次工具调用 · deny-by-default Tool Contract</div>
    <section class="detail-section">
      ${calls.map((tool, index) => `
        <div class="tool-card">
          <div class="tool-head">
            <strong>${index + 1}. ${escapeHtml(tool.tool_name)}</strong>
            <span class="status-pill ${className(tool.status)}">${escapeHtml(tool.status)}</span>
          </div>
          <p>${tool.latency_ms ?? "—"} ms${tool.error_message ? ` · ${escapeHtml(tool.error_message)}` : ""}</p>
        </div>`).join("") || '<div class="inspector-empty"><h3>未调用工具</h3><p>Unsupported 或安全前置门禁可能在工具执行前结束流程。</p></div>'}
    </section>`;
}

async function loadSessions({ selectFirst = false } = {}) {
  state.demoData = false;
  try {
    const sessions = await apiFetch("/api/v2/sessions", { method: "GET" });
    state.sessions = sessions;
    renderSessions();
    if (selectFirst && sessions.length) await selectSession(sessions[0].id);
    toast(`已加载 ${sessions.length} 个会话`);
  } catch (error) {
    toast(`无法连接后端：${error.message}`, "error");
  }
}

async function createSession() {
  const title = window.prompt("新会话名称", "GitHub 演示会话");
  if (!title?.trim()) return;
  try {
    const session = await apiFetch("/api/v2/sessions", {
      method: "POST",
      body: JSON.stringify({ title: title.trim() }),
    });
    state.sessions.unshift(session);
    renderSessions();
    await selectSession(session.id);
    toast("会话创建成功");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function selectSession(sessionId) {
  const session = state.sessions.find((item) => item.id === sessionId);
  if (!session) return;
  state.activeSession = session;
  state.activeRun = null;
  renderSessions();
  elements.activeSessionTitle.textContent = session.title;
  elements.activeSessionMeta.textContent = session.demo ? "明确标注的静态界面示例，不代表真实运行结果" : `Session ${String(session.id).slice(0, 8)} · 本地 UUID 隔离 RAGFlow ID`;
  setStatus(elements.activeStatus, session.demo ? "示例数据" : "active", session.demo ? "review_required" : "success");
  elements.deleteSessionButton.classList.toggle("hidden", Boolean(session.demo));
  elements.approvalCard.classList.add("hidden");
  renderInspector();

  if (session.demo) {
    renderHistory(session.items);
    state.activeRun = session.items.at(-1);
    showApproval(state.activeRun);
    renderInspector();
    return;
  }

  try {
    const history = await apiFetch(`/api/v2/sessions/${sessionId}/history?page=1&page_size=30`, { method: "GET" });
    renderHistory(history.items || []);
    if (history.items?.length) await loadRun(history.items.at(-1).run_id, { quiet: true });
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteSession() {
  if (!state.activeSession || state.activeSession.demo) return;
  if (!window.confirm(`确认软删除会话“${state.activeSession.title}”？`)) return;
  try {
    await apiFetch(`/api/v2/sessions/${state.activeSession.id}`, { method: "DELETE" });
    state.sessions = state.sessions.filter((item) => item.id !== state.activeSession.id);
    state.activeSession = null;
    state.activeRun = null;
    renderSessions();
    renderHistory([]);
    renderInspector();
    elements.activeSessionTitle.textContent = "EvidenceOps 演示台";
    elements.activeSessionMeta.textContent = "证据可信度驱动的企业知识 Agent";
    setStatus(elements.activeStatus, "未连接", "neutral");
    elements.deleteSessionButton.classList.add("hidden");
    toast("会话已软删除");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function ensureSession() {
  if (state.activeSession && !state.activeSession.demo) return state.activeSession;
  const session = await apiFetch("/api/v2/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "EvidenceOps 演示会话" }),
  });
  state.sessions = [session, ...state.sessions.filter((item) => !item.demo)];
  await selectSession(session.id);
  return session;
}

async function runAgent(query, payload = {}) {
  setLoading(true);
  appendThinking();
  try {
    const session = await ensureSession();
    const idempotencyKey = `ui-${newId()}`;
    const run = await apiFetch("/api/v2/agent/runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        session_id: session.id,
        query,
        document_a: payload.document_a || null,
        document_b: payload.document_b || null,
      }),
    });
    run._query = query;
    state.activeRun = run;
    appendRun(query, run);
    showApproval(run);
    renderInspector();
    setStatus(elements.activeStatus, run.status, run.status);
    toast(`Run ${String(run.run_id).slice(0, 8)} · ${run.status}`);
  } catch (error) {
    document.querySelector("#thinking-message")?.remove();
    toast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

async function loadRun(runId, { quiet = false } = {}) {
  if (state.demoData) {
    const demoRun = state.activeSession?.items?.find((item) => item.run_id === runId);
    if (demoRun) {
      state.activeRun = demoRun;
      showApproval(demoRun);
      renderInspector();
    }
    return;
  }
  try {
    const run = await apiFetch(`/api/v2/agent/runs/${runId}`, { method: "GET" });
    state.activeRun = run;
    showApproval(run);
    renderInspector();
    if (!quiet) toast(`已加载 Run ${String(runId).slice(0, 8)}`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function resumeRun(approved) {
  if (!state.activeRun || state.activeRun.status !== "pending_approval") return;
  setLoading(true);
  try {
    const run = await apiFetch(`/api/v2/agent/runs/${state.activeRun.run_id}/resume`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        comment: elements.approvalComment.value.trim() || null,
      }),
    });
    state.activeRun = run;
    elements.approvalCard.classList.add("hidden");
    appendRun(approved ? "批准并提交反馈" : "拒绝本次反馈", run);
    renderInspector();
    setStatus(elements.activeStatus, run.status, run.status);
    toast(approved ? "反馈已批准并完成提交" : "已拒绝本次反馈");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setLoading(false);
  }
}

function loadShowcaseSample() {
  const now = new Date().toISOString();
  const sample = {
    run_id: "7a31d3d8-39a5-4f1a-bb62-evidence-demo",
    session_id: "49e2c4ae-f13d-4fd2-demo-session",
    query: "公司的采购审批流程是什么？",
    status: "completed",
    intent: "knowledge_qa",
    answer: "采购申请需要先由申请人填写用途和预算，随后依次经过部门负责人、财务和相应权限人的审批。具体金额阈值请以《采购管理制度》第 3 页为准。",
    citations: [{ document_name: "采购管理制度.pdf", page: 3, score: 0.91, content: "采购申请应填写用途与预算……" }],
    evidence_passport: {
      policy_version: "evidence-passport/v1",
      task: "knowledge_qa",
      verdict: "grounded",
      confidence: 0.7127,
      high_risk: false,
      coverage: { citation_count: 1, source_count: 1, scored_citation_count: 1, average_score: 0.91, top_score: 0.91 },
      risk_flags: [],
      reasons: ["引用数量、相关度和来源覆盖满足当前策略"],
      recommended_action: "answer",
      recovery_plan: ["none"],
      evidence: [{ evidence_id: "70a9b0ef26868e95", source_id: "chunk-123", document_name: "采购管理制度.pdf", page: 3, score: 0.91, snippet: "采购申请应填写用途和预算，并按照金额权限逐级审批。", quarantined: false }],
    },
    tool_calls: [
      { tool_name: "knowledge_search", status: "success", latency_ms: 93, error_message: null },
      { tool_name: "evidence_guard", status: "success", latency_ms: 2, error_message: null },
    ],
    harness: {
      versions: { app_version: "3.1.0-harness", prompt_version: "prompt-v3", policy_version: "evidence-policy-v3", dataset_version: "2026-08-05.v2" },
      budget: { max_steps: 8, max_tool_calls: 6, within_token_budget: true },
      context: { turns_kept: 1, poisoned_turns_removed: 0, truncated: false },
      model_routes: [{ selected_model: "qwen3.8-max", fallback_used: false }],
      termination_reason: "completed",
      events: [
        { event_type: "lifecycle", name: "agent.run_started", status: "success", duration_ms: null },
        { event_type: "context", name: "context.prepared", status: "success", duration_ms: 1 },
        { event_type: "span", name: "llm.classify_intent", status: "success", duration_ms: 714 },
        { event_type: "span", name: "tool.knowledge_search", status: "success", duration_ms: 93 },
        { event_type: "policy", name: "tool.evidence_guard", status: "success", duration_ms: 2 },
        { event_type: "termination", name: "agent.terminated", status: "success", duration_ms: null },
      ],
    },
    created_at: now,
  };
  state.demoData = true;
  state.sessions = [{ id: sample.session_id, title: "采购制度问答 · 示例", status: "active", created_at: now, demo: true, items: [sample] }];
  state.activeSession = state.sessions[0];
  state.activeRun = sample;
  renderSessions();
  selectSession(sample.session_id);
  toast("当前为明确标注的界面示例数据");
}

elements.refreshButton.addEventListener("click", () => loadSessions({ selectFirst: !state.activeSession }));
elements.sampleButton.addEventListener("click", loadShowcaseSample);
elements.newSessionButton.addEventListener("click", createSession);
elements.deleteSessionButton.addEventListener("click", deleteSession);
elements.approveButton.addEventListener("click", () => resumeRun(true));
elements.rejectButton.addEventListener("click", () => resumeRun(false));

elements.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = elements.queryInput.value.trim();
  if (!query || state.loading) return;
  const payload = state.pendingScenario || {};
  elements.queryInput.value = "";
  state.pendingScenario = null;
  document.querySelectorAll(".scenario-chip").forEach((chip) => chip.classList.remove("active"));
  resizeComposer();
  runAgent(query, payload);
});

elements.queryInput.addEventListener("input", () => {
  resizeComposer();
  if (state.pendingScenario && elements.queryInput.value !== state.pendingScenario.query) {
    state.pendingScenario = null;
    document.querySelectorAll(".scenario-chip").forEach((chip) => chip.classList.remove("active"));
  }
});

elements.queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});

document.querySelectorAll(".scenario-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const scenario = scenarios[chip.dataset.scenario];
    if (!scenario) return;
    state.pendingScenario = scenario;
    elements.queryInput.value = scenario.query;
    resizeComposer();
    document.querySelectorAll(".scenario-chip").forEach((item) => item.classList.toggle("active", item === chip));
    elements.queryInput.focus();
  });
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.activeTab = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    renderInspector();
  });
});

elements.devUser.addEventListener("change", () => {
  localStorage.setItem("evidenceops-dev-user", elements.devUser.value.trim());
});

const savedUser = localStorage.getItem("evidenceops-dev-user");
if (savedUser) elements.devUser.value = savedUser;
elements.apiOrigin.textContent = window.location.origin;
resizeComposer();

if (new URLSearchParams(window.location.search).get("sample") === "1") {
  loadShowcaseSample();
}
