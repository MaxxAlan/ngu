// ============================================================
// local-agents · app.js  — rebuilt chat UI
// ============================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ---- DOM refs -------------------------------------------------------
const messagesEl    = $("#messages");
const welcomeEl     = $("#welcomeScreen");
const promptEl      = $("#prompt");
const submitBtn     = $("#submitBtn");
const submitLabel   = $("#submitLabel");
const formError     = $("#formError");
const statusDot     = $("#statusDot");
const statusText    = $("#statusText");
const modelSelect   = $("#modelSelect");
const modelBadge    = $("#modelBadge");
const rpModelSelect = $("#rpModelSelect");
const rpModelBadge  = $("#rpModelBadge");
const rpModelRole   = $("#rpModelRole");
const historyList   = $("#historyList");
const chatTitle     = $(".chat-title-text");
const dropzone      = $("#dropzone");
const fileInput     = $("#fileInput");
const attachmentsEl = $("#attachments");
const sidebarEl     = $("#sidebar");
const rightPanel    = $("#rightPanel");

// ---- State ----------------------------------------------------------
const state = {
  filter: "",
  tasks: new Map(),       // id → task
  activeTaskId: null,     // currently-displayed task
  attachments: [],
  uploading: 0,
  defaultModel: "",
  streamBubble: null,     // DOM node for streaming bubble
  cacheHits: 0,
};

// ---- Persist model preference in localStorage ----------------------
function savedModel() { return localStorage.getItem("la_model") || ""; }
function saveModel(v) { localStorage.setItem("la_model", v); }

// ---- API helper -----------------------------------------------------
async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const headers = isForm ? {} : { "Content-Type": "application/json" };
  const res = await fetch(path, { headers, ...options });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* non-json */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

// ---- Formatting helpers ---------------------------------------------
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const diff = now - d;
  if (diff < 60_000) return "just now";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)}h ago`;
  return d.toLocaleDateString();
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function normalizeTask(t) {
  for (const key of ["attachments", "steps"]) {
    if (typeof t[key] === "string") {
      try { t[key] = JSON.parse(t[key]); } catch { t[key] = []; }
    }
    if (!Array.isArray(t[key])) t[key] = [];
  }
  return t;
}

function fileKind(path, fallback) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png","jpg","jpeg","gif","webp","bmp"].includes(ext)) return "image";
  if (["txt","md","py","js","ts","json","yaml","yml","toml","html","css",
       "csv","xml","ini","cfg","log","sh","ps1","bat","cmd","rs","go",
       "java","c","h","cpp","hpp"].includes(ext)) return "text";
  return fallback || "file";
}

// ---- Model badge helper ---------------------------------------------
function modelBadgeText(name) {
  if (!name || name === "auto" || name === "") return "🔀AUTO";
  const low = name.toLowerCase();
  if (low.includes("vision") || low.includes("vl")) return "👁️Vision";
  if (low.includes("7b") || low.includes("8b")) return "🧠7B";
  if (low.includes("3b") || low.includes("4b")) return "🐤3B";
  if (low.includes("1.5b") || low.includes("1b")) return "🐤1.5B";
  return "🧠" + name.split(/[-_]/)[0].slice(0, 6);
}

function modelRoleText(name) {
  if (!name || name === "auto" || name === "") return "auto-routing";
  const low = name.toLowerCase();
  if (low.includes("vision") || low.includes("vl")) return "vision · multimodal";
  if (low.includes("7b") || low.includes("8b")) return "master · large";
  if (low.includes("3b") || low.includes("4b")) return "master · medium";
  if (low.includes("1.5b") || low.includes("1b")) return "scout · small";
  if (low.includes("coder")) return "code specialist";
  return "general";
}

function updateModelBadges(val) {
  const badge = modelBadgeText(val);
  const role  = modelRoleText(val);
  if (modelBadge)   modelBadge.textContent  = badge;
  if (rpModelBadge) rpModelBadge.textContent = badge;
  if (rpModelRole)  rpModelRole.textContent  = role;
}

// ---- Markdown renderer (simple, no library) ------------------------
function renderMarkdown(text) {
  if (!text) return "";

  // Escape HTML first
  const esc = (s) => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

  // Fenced code blocks  ```lang\n...\n```
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const cls = lang ? ` class="language-${esc(lang)}"` : "";
    return `<pre class="md-codeblock"><code${cls}>${esc(code.trim())}</code></pre>`;
  });

  // Inline code `...`
  text = text.replace(/`([^`\n]+)`/g, (_, code) => `<code class="md-inline">${esc(code)}</code>`);

  // Headers
  text = text.replace(/^#### (.+)$/gm, (_, t) => `<h4 class="md-h4">${t}</h4>`);
  text = text.replace(/^### (.+)$/gm,  (_, t) => `<h3 class="md-h3">${t}</h3>`);
  text = text.replace(/^## (.+)$/gm,   (_, t) => `<h2 class="md-h2">${t}</h2>`);
  text = text.replace(/^# (.+)$/gm,    (_, t) => `<h1 class="md-h1">${t}</h1>`);

  // Bold **text** / __text__
  text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__(.+?)__/g, "<strong>$1</strong>");

  // Italic *text* / _text_
  text = text.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  text = text.replace(/_([^_\n]+)_/g, "<em>$1</em>");

  // Strikethrough ~~text~~
  text = text.replace(/~~(.+?)~~/g, "<del>$1</del>");

  // Unordered list items
  text = text.replace(/^[-*+] (.+)$/gm, "<li>$1</li>");
  text = text.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul class=\"md-ul\">$1</ul>");

  // Ordered list items
  text = text.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");

  // Horizontal rule
  text = text.replace(/^---+$/gm, "<hr class=\"md-hr\">");

  // Blockquote
  text = text.replace(/^> (.+)$/gm, "<blockquote class=\"md-quote\">$1</blockquote>");

  // Paragraphs — blank line = new paragraph
  const lines = text.split("\n");
  const out = [];
  let inBlock = false;
  for (const line of lines) {
    if (line.match(/^<(h[1-4]|pre|ul|ol|hr|blockquote)/)) { inBlock = false; out.push(line); continue; }
    if (line.match(/^<\/(h[1-4]|pre|ul|ol|hr|blockquote)/)) { inBlock = false; out.push(line); continue; }
    if (line.trim() === "") { if (inBlock) { out.push("</p>"); inBlock = false; } continue; }
    if (!inBlock) { out.push("<p class=\"md-p\">"); inBlock = true; }
    out.push(line + " ");
  }
  if (inBlock) out.push("</p>");

  return out.join("\n");
}

// ---- Chat bubble builders ------------------------------------------
function createBubble(role, text, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${role}`;
  if (opts.taskId) wrap.dataset.taskId = opts.taskId;

  if (role === "user") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    if (opts.attachments?.length) {
      const atts = document.createElement("div");
      atts.className = "bubble-attachments";
      for (const a of opts.attachments) {
        const chip = document.createElement("span");
        chip.className = `file-chip ${fileKind(a.path || a, "")}`;
        chip.textContent = a.name || a.path || a;
        atts.append(chip);
      }
      bubble.append(atts);
    }
    wrap.append(bubble);

  } else if (role === "agent") {
    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = "◉";
    const content = document.createElement("div");
    content.className = "msg-content";
    if (opts.model) {
      const badge = document.createElement("span");
      badge.className = "msg-model-badge";
      badge.textContent = modelBadgeText(opts.model);
      content.append(badge);
    }
    const rendered = document.createElement("div");
    rendered.className = "md-rendered";
    rendered.innerHTML = renderMarkdown(text);
    content.append(rendered);

    // Actions bar
    const actions = document.createElement("div");
    actions.className = "bubble-actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "action-btn";
    copyBtn.textContent = "⧉ Copy";
    copyBtn.onclick = async () => {
      await navigator.clipboard.writeText(text).catch(() => {});
      copyBtn.textContent = "✓ Copied";
      setTimeout(() => { copyBtn.textContent = "⧉ Copy"; }, 1200);
    };
    actions.append(copyBtn);
    content.append(actions);

    wrap.append(avatar, content);

  } else if (role === "tool") {
    const det = document.createElement("details");
    det.className = "tool-call-details";
    const sum = document.createElement("summary");
    sum.className = "tool-call-summary";
    sum.innerHTML = `<span class="tc-icon">⚙</span> <code class="tc-name">${escHtml(opts.name || "tool")}</code>`;
    if (text) {
      sum.innerHTML += ` <span class="tc-args">${escHtml(shortArgs(text))}</span>`;
    }
    det.append(sum);
    if (text) {
      const pre = document.createElement("pre");
      pre.className = "tc-args-full";
      pre.textContent = text;
      det.append(pre);
    }
    wrap.append(det);

  } else if (role === "tool-result") {
    const det = document.createElement("details");
    det.className = `tool-result-details${opts.error ? " error" : ""}`;
    const sum = document.createElement("summary");
    sum.className = "tool-result-summary";
    sum.textContent = opts.error ? `✗ ${opts.name || "result"}` : `✓ ${opts.name || "result"}`;
    det.append(sum);
    const pre = document.createElement("pre");
    pre.className = "tc-result-pre";
    pre.textContent = text;
    det.append(pre);
    wrap.append(det);

  } else if (role === "streaming") {
    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = "◉";
    const content = document.createElement("div");
    content.className = "msg-content streaming";
    const dots = document.createElement("span");
    dots.className = "streaming-dots";
    dots.innerHTML = "<span></span><span></span><span></span>";
    const liveText = document.createElement("div");
    liveText.className = "live-text md-rendered";
    content.append(dots, liveText);
    wrap.append(avatar, content);

  } else if (role === "system") {
    wrap.innerHTML = `<span class="sys-msg">${escHtml(text)}</span>`;
  }

  return wrap;
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function shortArgs(args) {
  if (!args) return "";
  const s = typeof args === "string" ? args : JSON.stringify(args);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}

// ---- Scroll helper --------------------------------------------------
function scrollBottom(smooth = true) {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: smooth ? "smooth" : "instant" });
}

function appendMsg(bubble) {
  messagesEl.append(bubble);
  scrollBottom();
}

// ---- Welcome screen -------------------------------------------------
function showWelcome() {
  welcomeEl?.classList.remove("hidden");
}

function hideWelcome() {
  welcomeEl?.classList.add("hidden");
}

// ---- Render a full task as a chat thread ---------------------------
function renderTaskThread(t) {
  // Clear messages
  messagesEl.replaceChildren();
  hideWelcome();

  // User prompt bubble
  const userBubble = createBubble("user", t.prompt, {
    taskId: t.id,
    attachments: t.attachments || [],
  });
  appendMsg(userBubble);

  // Steps → agent/tool bubbles
  const steps = t.steps || [];
  for (const s of steps) {
    // Agent content
    if (s.content) {
      const b = createBubble("agent", s.content, { model: t.model, taskId: t.id });
      b.dataset.stepIndex = s.index;
      appendMsg(b);
    }
    // Tool calls
    for (const tc of (s.tool_calls || [])) {
      const args = typeof tc.arguments === "object"
        ? JSON.stringify(tc.arguments, null, 2)
        : (tc.arguments || "");
      const b = createBubble("tool", args, { name: tc.name, taskId: t.id });
      appendMsg(b);
    }
    // Tool results
    for (const tr of (s.tool_results || [])) {
      const b = createBubble("tool-result", tr.result || tr.error || "", {
        name: tr.name,
        error: !!tr.error,
        taskId: t.id,
      });
      appendMsg(b);
    }
  }

  // Final result (if no steps but result exists)
  if (t.result && !steps.length) {
    const b = createBubble("agent", t.result, { model: t.model, taskId: t.id });
    appendMsg(b);
  }

  // Error
  if (t.error) {
    const b = createBubble("system", `Error: ${t.error}`);
    b.classList.add("error");
    appendMsg(b);
  }

  // Streaming bubble placeholder if still running
  if (t.status === "running" || t.status === "queued") {
    state.streamBubble = createBubble("streaming");
    state.streamBubble.dataset.taskId = t.id;
    appendMsg(state.streamBubble);
  }

  // Update title
  chatTitle.textContent = t.title || t.prompt?.slice(0, 50) || t.id;
  scrollBottom(false);
}

// ---- Switch active task ---------------------------------------------
function openTask(taskId) {
  const t = state.tasks.get(taskId);
  if (!t) return;
  state.activeTaskId = taskId;
  state.streamBubble = null;
  renderTaskThread(t);
  // Highlight in sidebar
  $$(".history-item").forEach(el => el.classList.toggle("active", el.dataset.id === taskId));
}

// ---- History sidebar ------------------------------------------------
function buildHistoryItem(t) {
  const tpl = $("#historyItemTpl").content.firstElementChild.cloneNode(true);
  tpl.dataset.id = t.id;

  const pill = tpl.querySelector(".hi-pill");
  pill.textContent = t.status;
  pill.className = `hi-pill ${t.status}`;

  tpl.querySelector(".hi-title").textContent = t.title || t.prompt?.slice(0, 40) || t.id;
  tpl.querySelector(".hi-time").textContent = fmtTime(t.finished_at || t.started_at || t.created_at);

  tpl.querySelector(".hi-delete").onclick = async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete task ${t.id}?`)) return;
    await api(`/api/tasks/${t.id}`, { method: "DELETE" }).catch(() => {});
    state.tasks.delete(t.id);
    if (state.activeTaskId === t.id) {
      state.activeTaskId = null;
      messagesEl.replaceChildren();
      showWelcome();
      chatTitle.textContent = "New Chat";
    }
    renderHistoryList();
  };

  tpl.addEventListener("click", () => openTask(t.id));
  if (t.status === "running") tpl.classList.add("running");
  return tpl;
}

function renderHistoryList() {
  const items = [...state.tasks.values()]
    .filter(t => !state.filter || t.status === state.filter)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  historyList.replaceChildren();
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "empty-history";
    p.textContent = state.filter ? `No ${state.filter} tasks` : "No tasks yet";
    historyList.append(p);
    return;
  }
  for (const t of items) historyList.append(buildHistoryItem(t));

  // Re-apply active state
  if (state.activeTaskId) {
    $$(".history-item").forEach(el => el.classList.toggle("active", el.dataset.id === state.activeTaskId));
  }
}

// ---- Health & status ------------------------------------------------
async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const counts = h.counts || {};
    const llama  = h.llama  || {};
    state.defaultModel = h.default_model || state.defaultModel;

    statusDot.className = `dot ${llama.ok ? "ok" : "err"}`;
    statusText.textContent = llama.ok
      ? `${(llama.models || []).length} model · ${llama.device || "cpu"} · ${counts.running || 0} running`
      : "llama.cpp offline";

    // Right panel
    $("#rpLlamaStatus").textContent = llama.ok ? "online ✓" : "offline ✗";
    $("#rpLlamaStatus").className   = `status-val ${llama.ok ? "ok" : "err"}`;
    $("#rpDevice").textContent  = llama.device || "—";
    $("#rpRunning").textContent = counts.running || 0;
    $("#rpQueued").textContent  = counts.queued  || 0;
    $("#totalTasks").textContent = (counts.done || 0) + (counts.running || 0) + (counts.queued || 0);

    // Active model cards
    const modelsEl = $("#activeModels");
    const loaded = llama.models || [];
    if (loaded.length) {
      modelsEl.replaceChildren();
      for (const m of loaded) {
        const card = document.createElement("div");
        card.className = "active-model-card";
        card.innerHTML = `
          <span class="amc-badge">${escHtml(modelBadgeText(m.name))}</span>
          <div class="amc-info">
            <span class="amc-name">${escHtml(m.name)}</span>
            <span class="amc-role">${escHtml(modelRoleText(m.name))}</span>
          </div>`;
        modelsEl.append(card);
      }
    } else {
      modelsEl.innerHTML = `<p class="rp-empty">No models loaded</p>`;
    }
  } catch {
    statusDot.className = "dot err";
    statusText.textContent = "server offline";
  }
}

// ---- Model loading --------------------------------------------------
async function loadModels() {
  const pref = savedModel();
  try {
    const models = await api("/api/models");
    // Sync both selects
    for (const sel of [modelSelect, rpModelSelect]) {
      const cur = sel.value;
      sel.replaceChildren();
      // Auto option
      const autoOpt = new Option("🔀 Auto (recommended)", "");
      sel.append(autoOpt);
      for (const m of models) {
        const bits = [m.name];
        if (m.vision) bits.push("👁 vision");
        if (m.parameter_size) bits.push(m.parameter_size);
        const opt = new Option(bits.join(" · "), m.name);
        sel.append(opt);
      }
      // Restore preference
      const target = pref || state.defaultModel;
      if (target && [...sel.options].some(o => o.value === target)) {
        sel.value = target;
      } else if (cur && [...sel.options].some(o => o.value === cur)) {
        sel.value = cur;
      }
    }
    updateModelBadges(modelSelect.value);
  } catch {
    for (const sel of [modelSelect, rpModelSelect]) {
      sel.replaceChildren(new Option("llama.cpp offline", ""));
    }
  }
}

// Keep selects in sync
function syncSelects(source, target) {
  target.value = source.value;
  updateModelBadges(source.value);
  saveModel(source.value);
}

modelSelect.addEventListener("change", () => syncSelects(modelSelect, rpModelSelect));
rpModelSelect.addEventListener("change", () => syncSelects(rpModelSelect, modelSelect));

// ---- Task loading ---------------------------------------------------
async function loadTasks() {
  const qs = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
  const list = (await api(`/api/tasks${qs}`)).map(normalizeTask);
  state.tasks = new Map(list.map(t => [t.id, t]));
  renderHistoryList();
  // Refresh current view if task was updated
  if (state.activeTaskId && state.tasks.has(state.activeTaskId)) {
    renderTaskThread(state.tasks.get(state.activeTaskId));
  }
}

// ---- New chat -------------------------------------------------------
function newChat() {
  state.activeTaskId = null;
  state.streamBubble = null;
  messagesEl.replaceChildren();
  showWelcome();
  chatTitle.textContent = "New Chat";
  promptEl.value = "";
  state.attachments = [];
  renderAttachments();
  promptEl.focus();
  $$(".history-item").forEach(el => el.classList.remove("active"));
}

$("#newChatBtn").onclick = newChat;

// ---- Submit ---------------------------------------------------------
async function submitTask() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  if (state.uploading) {
    showError("Still uploading files, please wait…");
    return;
  }
  submitBtn.disabled = true;
  submitLabel.textContent = "Sending…";
  formError.hidden = true;

  // Optimistic: show user bubble immediately
  hideWelcome();
  const userBubble = createBubble("user", prompt, { attachments: [...state.attachments] });
  appendMsg(userBubble);
  state.streamBubble = createBubble("streaming");
  appendMsg(state.streamBubble);

  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        title: $("#taskTitle").value.trim(),
        prompt,
        agent: $("#agentInput").value.trim() || null,
        model: modelSelect.value || null,
        auto_run: $("#autoRun").checked,
        attachments: state.attachments.map(f => f.path),
      }),
    });
    state.activeTaskId = task.id;
    normalizeTask(task);
    state.tasks.set(task.id, task);
    renderHistoryList();

    // Update the streaming bubble's task id
    if (state.streamBubble) state.streamBubble.dataset.taskId = task.id;

    // Update chat title
    chatTitle.textContent = task.title || prompt.slice(0, 50);

    // Clear input
    promptEl.value = "";
    $("#taskTitle").value = "";
    state.attachments = [];
    renderAttachments();
    refreshHealth();
  } catch (err) {
    // Remove optimistic bubbles
    userBubble.remove();
    state.streamBubble?.remove();
    state.streamBubble = null;
    showError(err.message);
  } finally {
    submitBtn.disabled = false;
    submitLabel.textContent = "Send";
  }
}

function showError(msg) {
  formError.hidden = false;
  formError.textContent = msg;
}

submitBtn.onclick = submitTask;
promptEl.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); submitTask(); }
  if (e.key === "Escape") { promptEl.value = ""; formError.hidden = true; }
});

// Quick start buttons
document.addEventListener("click", (e) => {
  const qb = e.target.closest(".quick-btn, .qtool-btn");
  if (!qb) return;
  const p = qb.dataset.prompt;
  if (p) {
    promptEl.value = p;
    promptEl.focus();
    // Scroll textarea into view
    promptEl.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});

// ---- SSE / streaming -----------------------------------------------
function connectEvents() {
  const es = new EventSource("/api/events");

  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }

    if (data.type === "task") {
      const merged = normalizeTask({ ...state.tasks.get(data.task.id), ...data.task });
      state.tasks.set(data.task.id, merged);
      renderHistoryList();
      refreshHealth();

      // If this is the active task, update it
      if (state.activeTaskId === data.task.id) {
        // If task just finished, re-render full thread and remove stream bubble
        if (["done", "failed", "cancelled"].includes(data.task.status)) {
          state.streamBubble = null;
          renderTaskThread(merged);
        }
      }
      return;
    }

    if (data.type === "token") {
      const t = state.tasks.get(data.task_id);
      if (!t) return;
      t.partial = (t.partial || "") + data.delta;

      // Only update UI if this is the active task
      if (state.activeTaskId !== data.task_id) return;

      // Find or create streaming bubble
      let sb = state.streamBubble;
      if (!sb || sb.dataset.taskId !== data.task_id) {
        // Find in DOM
        sb = messagesEl.querySelector(`.msg-streaming[data-task-id="${data.task_id}"], .msg[data-task-id="${data.task_id}"] .streaming`);
        if (!sb) {
          state.streamBubble = createBubble("streaming");
          state.streamBubble.dataset.taskId = data.task_id;
          appendMsg(state.streamBubble);
          sb = state.streamBubble;
        }
      }
      const liveTextEl = sb.querySelector(".live-text");
      const dotsEl     = sb.querySelector(".streaming-dots");
      if (liveTextEl) {
        dotsEl?.remove(); // hide dots once text arrives
        liveTextEl.innerHTML = renderMarkdown(t.partial);
        scrollBottom(false);
      }
      return;
    }

    if (data.type === "agent_step") {
      const t = state.tasks.get(data.task_id);
      if (!t) return;
      t.steps = t.steps || [];
      t.partial = "";

      const existing = t.steps.find(s => s.index === data.step);
      const step = existing || { index: data.step, content: "", tool_calls: [], tool_results: [] };
      step.content     = data.content     || step.content;
      step.tool_calls  = data.tool_calls  || step.tool_calls;
      if (!existing) t.steps.push(step);

      if (state.activeTaskId !== data.task_id) return;

      // Remove streaming bubble
      if (state.streamBubble) {
        state.streamBubble.remove();
        state.streamBubble = null;
      }

      // Add agent content bubble if content exists
      if (step.content) {
        // Check if bubble for this step index already exists
        const existingBubble = messagesEl.querySelector(`.msg-agent[data-step-index="${step.index}"][data-task-id="${data.task_id}"]`);
        if (existingBubble) {
          existingBubble.querySelector(".md-rendered").innerHTML = renderMarkdown(step.content);
        } else {
          const b = createBubble("agent", step.content, { model: t.model, taskId: data.task_id });
          b.dataset.stepIndex = step.index;
          appendMsg(b);
        }
      }

      // Add tool call bubbles
      for (const tc of (step.tool_calls || [])) {
        const key = `${step.index}-${tc.name}`;
        if (!messagesEl.querySelector(`.msg-tool[data-tc-key="${key}"]`)) {
          const args = typeof tc.arguments === "object"
            ? JSON.stringify(tc.arguments, null, 2)
            : (tc.arguments || "");
          const b = createBubble("tool", args, { name: tc.name, taskId: data.task_id });
          b.dataset.tcKey = key;
          appendMsg(b);
        }
      }

      // New streaming bubble
      state.streamBubble = createBubble("streaming");
      state.streamBubble.dataset.taskId = data.task_id;
      appendMsg(state.streamBubble);
      return;
    }

    if (data.type === "tool_result") {
      const t = state.tasks.get(data.task_id);
      if (!t) return;
      const step = (t.steps || []).find(s => s.index === data.step);
      if (step) {
        step.tool_results = step.tool_results || [];
        step.tool_results.push({ name: data.name, result: data.result, error: data.error });
      }

      if (state.activeTaskId !== data.task_id) return;

      // Remove streaming bubble
      if (state.streamBubble) {
        state.streamBubble.remove();
        state.streamBubble = null;
      }

      const b = createBubble("tool-result", data.result || data.error || "", {
        name: data.name,
        error: !!data.error,
        taskId: data.task_id,
      });
      appendMsg(b);

      // New streaming bubble for next token stream
      state.streamBubble = createBubble("streaming");
      state.streamBubble.dataset.taskId = data.task_id;
      appendMsg(state.streamBubble);
    }
  };

  es.onerror = () => {
    statusDot.className = "dot err";
    statusText.textContent = "connection lost, reconnecting…";
  };
}

// ---- Attachments ----------------------------------------------------
function renderAttachments() {
  attachmentsEl.replaceChildren();
  attachmentsEl.hidden = state.attachments.length === 0;
  for (const file of state.attachments) {
    const chip = document.createElement("span");
    chip.className = `file-chip ${file.kind || "binary"}`;
    const label = document.createElement("span");
    label.className = "file-name";
    label.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "file-meta";
    meta.textContent = `${file.kind || "file"} · ${fmtSize(file.size)}`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "file-remove";
    rm.textContent = "✕";
    rm.onclick = () => {
      state.attachments = state.attachments.filter(f => f.path !== file.path);
      renderAttachments();
    };
    chip.append(label, meta, rm);
    attachmentsEl.append(chip);
  }
}

async function uploadFiles(files) {
  const list = [...files].filter(Boolean);
  if (!list.length) return;
  state.uploading += 1;
  dropzone.classList.add("busy");
  formError.hidden = true;
  try {
    const fd = new FormData();
    for (const f of list) fd.append("files", f, f.name);
    const data = await api("/api/upload", { method: "POST", body: fd });
    const known = new Set(state.attachments.map(f => f.path));
    for (const f of data.files || []) if (!known.has(f.path)) state.attachments.push(f);
    renderAttachments();
  } catch (err) {
    showError(`Upload error: ${err.message}`);
  } finally {
    state.uploading -= 1;
    if (!state.uploading) dropzone.classList.remove("busy");
  }
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", async () => { await uploadFiles(fileInput.files); fileInput.value = ""; });

for (const evt of ["dragenter", "dragover"]) {
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("over"); });
}
for (const evt of ["dragleave", "dragend"]) {
  dropzone.addEventListener(evt, (e) => { if (e.target === dropzone) dropzone.classList.remove("over"); });
}
dropzone.addEventListener("drop", async (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  await uploadFiles(e.dataTransfer.files);
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.target.closest("#dropzone")) return;
  uploadFiles(e.dataTransfer.files);
});
promptEl.addEventListener("paste", async (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  await uploadFiles(files);
});

// ---- History filter chips ------------------------------------------
$("#historyFilters").addEventListener("click", async (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $$(".filter-chips .chip").forEach(c => c.classList.toggle("active", c === chip));
  state.filter = chip.dataset.status;
  await loadTasks();
});

// ---- Sidebar & right panel toggles ---------------------------------
$("#sidebarToggle").onclick = () => sidebarEl.classList.toggle("collapsed");
$("#rightPanelToggle").onclick = () => rightPanel.classList.toggle("collapsed");

// ---- Reload models -------------------------------------------------
$("#reloadModelsBtn").onclick = async () => {
  $("#reloadModelsBtn").textContent = "⏳ scanning…";
  await loadModels();
  await refreshHealth();
  $("#reloadModelsBtn").textContent = "🔄 Rescan models";
};

// ---- Init ----------------------------------------------------------
(async function init() {
  showWelcome();
  renderAttachments();
  await refreshHealth();
  await loadModels();
  await loadTasks();
  connectEvents();
  setInterval(refreshHealth, 10_000);
})();
