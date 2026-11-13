const $ = (sel) => document.querySelector(sel);

const tasksEl = $("#tasks");
const emptyEl = $("#empty");
const tpl = $("#taskTpl");
const form = $("#taskForm");
const submitBtn = $("#submitBtn");
const formError = $("#formError");
const statusDot = $("#statusDot");
const statusText = $("#statusText");
const modelSelect = $("#model");
const dropzone = $("#dropzone");
const fileInput = $("#fileInput");
const attachmentsEl = $("#attachments");
const promptEl = $("#prompt");

const state = { filter: "", tasks: new Map(), attachments: [], uploading: 0, defaultModel: "" };

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const headers = isForm ? {} : { "Content-Type": "application/json" };
  const res = await fetch(path, { headers, ...options });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* non-json error body */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
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

// ---- attachments -------------------------------------------------------
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
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.textContent = "✕";
    remove.title = "bỏ file";
    remove.onclick = () => {
      state.attachments = state.attachments.filter((f) => f.path !== file.path);
      renderAttachments();
    };
    chip.append(label, meta, remove);
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
    const known = new Set(state.attachments.map((f) => f.path));
    for (const f of data.files || []) if (!known.has(f.path)) state.attachments.push(f);
    renderAttachments();
  } catch (err) {
    formError.hidden = false;
    formError.textContent = `upload lỗi: ${err.message}`;
  } finally {
    state.uploading -= 1;
    if (!state.uploading) dropzone.classList.remove("busy");
  }
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", async () => {
  await uploadFiles(fileInput.files);
  fileInput.value = "";
});

for (const evt of ["dragenter", "dragover"]) {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("over");
  });
}
for (const evt of ["dragleave", "dragend"]) {
  dropzone.addEventListener(evt, (e) => {
    if (e.target === dropzone) dropzone.classList.remove("over");
  });
}
dropzone.addEventListener("drop", async (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  await uploadFiles(e.dataTransfer.files);
});

// Prevent the browser from opening dropped files anywhere else.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.target.closest("#dropzone")) return;
  uploadFiles(e.dataTransfer.files);
});

// Paste screenshots straight into the prompt box.
promptEl.addEventListener("paste", async (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  await uploadFiles(files);
});

// ---- task rendering ----------------------------------------------------
function renderSteps(node, t) {
  const details = node.querySelector(".steps");
  const list = node.querySelector(".step-list");
  const steps = t.steps || [];
  if (!steps.length) {
    details.hidden = true;
    return;
  }
  details.hidden = false;
  node.querySelector(".step-count").textContent = steps.length;
  list.replaceChildren();
  for (const s of steps) {
    const wrap = document.createElement("div");
    wrap.className = "step";

    const head = document.createElement("div");
    head.className = "step-head";
    head.textContent = `step ${s.index ?? "?"}`;
    wrap.append(head);

    if (s.content) {
      const pre = document.createElement("pre");
      pre.className = "step-content";
      pre.textContent = s.content;
      wrap.append(pre);
    }

    for (const call of s.tool_calls || []) {
      const tc = document.createElement("div");
      tc.className = "tool-call";
      tc.textContent = `⚙ ${call.name || "?"} ${JSON.stringify(call.arguments || {})}`;
      wrap.append(tc);
    }

    for (const res of s.tool_results || []) {
      const pre = document.createElement("pre");
      pre.className = `tool-result${res.error ? " error" : ""}`;
      pre.textContent = res.error ? `${res.error}\n${res.result || ""}` : (res.result || "");
      wrap.append(pre);
    }

    list.append(wrap);
  }
}

function fileKind(path, fallback) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "image";
  if (["txt", "md", "py", "js", "ts", "json", "yaml", "yml", "toml", "html", "css",
       "csv", "xml", "ini", "cfg", "log", "sh", "ps1", "bat", "cmd", "rs", "go",
       "java", "c", "h", "cpp", "hpp"].includes(ext)) return "text";
  return fallback || "file";
}

function buildCard(t) {
  const node = tpl.content.firstElementChild.cloneNode(true);
  node.dataset.id = t.id;
  node.classList.add(t.status);

  const pill = node.querySelector(".pill");
  pill.textContent = t.status;
  pill.classList.add(t.status);
  node.querySelector(".task-title").textContent = t.title || t.id;
  node.querySelector(".meta-agent").textContent = `@${t.agent}`;
  node.querySelector(".meta-model").textContent = t.model;
  node.querySelector(".meta-time").textContent = fmtTime(t.finished_at || t.started_at || t.created_at);
  node.querySelector(".meta-id").textContent = t.id;
  node.querySelector(".task-prompt").textContent = t.prompt;

  const atts = t.attachments || [];
  if (atts.length) {
    const box = node.querySelector(".attachments");
    box.hidden = false;
    for (const raw of atts) {
      const a = typeof raw === "string" ? { path: raw, name: raw.split(/[\\/]/).pop() } : raw;
      const kind = fileKind(a.path || a.name || "", a.kind);
      const chip = document.createElement("span");
      chip.className = `file-chip ${kind}`;
      chip.textContent = `${a.name || a.path}${kind ? ` · ${kind}` : ""}`;
      chip.title = a.path || "";
      box.append(chip);
    }
  }

  renderSteps(node, t);

  const out = node.querySelector(".task-result");
  const body = t.result || t.partial || "";
  if (body && !["queued", "running"].includes(t.status)) {
    out.hidden = false;
    out.textContent = body;
  }
  if (t.error) {
    const err = node.querySelector(".task-error");
    err.hidden = false;
    err.textContent = t.error;
  }

  const runBtn = node.querySelector(".run");
  runBtn.disabled = t.status === "running" || t.status === "queued";
  runBtn.onclick = () => act(`/api/tasks/${t.id}/run`);

  const cancelBtn = node.querySelector(".cancel");
  cancelBtn.disabled = !["queued", "running"].includes(t.status);
  cancelBtn.onclick = () => act(`/api/tasks/${t.id}/cancel`);

  node.querySelector(".copy").onclick = async (ev) => {
    try {
      await navigator.clipboard.writeText(t.result || "");
      ev.currentTarget.textContent = "✓";
      setTimeout(() => (ev.currentTarget.textContent = "⧉"), 900);
    } catch { /* clipboard blocked */ }
  };

  node.querySelector(".delete").onclick = async () => {
    if (!confirm(`Xoá task ${t.id}?`)) return;
    await api(`/api/tasks/${t.id}`, { method: "DELETE" });
    state.tasks.delete(t.id);
    node.remove();
    toggleEmpty();
  };

  return node;
}

async function act(path) {
  try {
    await api(path, { method: "POST" });
  } catch (err) {
    alert(err.message);
  }
}

function matchesFilter(t) {
  return !state.filter || t.status === state.filter;
}

function renderTask(t) {
  const existing = tasksEl.querySelector(`.task[data-id="${t.id}"]`);
  if (!matchesFilter(t)) {
    if (existing) existing.remove();
    toggleEmpty();
    return;
  }
  const node = buildCard(t);
  if (existing) existing.replaceWith(node);
  else tasksEl.prepend(node);
  toggleEmpty();
}

function renderAll() {
  tasksEl.replaceChildren();
  const list = [...state.tasks.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  for (const t of list) if (matchesFilter(t)) tasksEl.append(buildCard(t));
  toggleEmpty();
}

function toggleEmpty() {
  emptyEl.hidden = tasksEl.querySelector(".task") !== null;
}

async function loadTasks() {
  const qs = state.filter ? `?status=${encodeURIComponent(state.filter)}` : "";
  const list = (await api(`/api/tasks${qs}`)).map(normalizeTask);
  state.tasks = new Map(list.map((t) => [t.id, t]));
  renderAll();
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const counts = h.counts || {};
    const llama = h.llama || {};
    state.defaultModel = h.default_model || state.defaultModel;
    statusDot.className = `dot ${llama.ok ? "ok" : "err"}`;
    statusText.textContent = llama.ok
      ? `${(llama.models || []).length} model · ${llama.device || "cpu"} · ${counts.running || 0} running · ${counts.queued || 0} queued`
      : "llama.cpp offline";
  } catch {
    statusDot.className = "dot err";
    statusText.textContent = "server offline";
  }
}

async function loadModels() {
  const currentVal = modelSelect.value;
  try {
    const models = await api("/api/models");
    modelSelect.replaceChildren();
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m.name;
      const bits = [m.name];
      if (m.vision) bits.push("vision");
      if (m.parameter_size) bits.push(m.parameter_size);
      opt.textContent = bits.join(" · ");
      modelSelect.append(opt);
    }
    if (currentVal && [...modelSelect.options].some((o) => o.value === currentVal)) {
      modelSelect.value = currentVal;
    } else if (state.defaultModel && [...modelSelect.options].some((o) => o.value === state.defaultModel)) {
      modelSelect.value = state.defaultModel;
    }
    if (!models.length) {
      modelSelect.append(new Option("chưa có model trong models/", ""));
    }
  } catch {
    modelSelect.replaceChildren(new Option("llama.cpp offline", ""));
  }
}

const reloadModelsBtn = $("#reloadModelsBtn");
if (reloadModelsBtn) {
  reloadModelsBtn.onclick = async () => {
    reloadModelsBtn.textContent = "⏳...";
    await loadModels();
    await refreshHealth();
    reloadModelsBtn.textContent = "🔄 quét lại";
  };
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }

    if (data.type === "task") {
      const merged = normalizeTask({ ...state.tasks.get(data.task.id), ...data.task });
      state.tasks.set(data.task.id, merged);
      renderTask(merged);
      refreshHealth();
      return;
    }

    if (data.type === "token") {
      const t = state.tasks.get(data.task_id);
      if (!t) return;
      t.partial = (t.partial || "") + data.delta;
      const card = tasksEl.querySelector(`.task[data-id="${data.task_id}"]`);
      const live = card?.querySelector(".live");
      const text = card?.querySelector(".live-text");
      if (!live || !text) return;
      live.hidden = false;
      text.textContent = t.partial;
      text.scrollTop = text.scrollHeight;
      return;
    }

    if (data.type === "agent_step" || data.type === "tool_result") {
      const t = state.tasks.get(data.task_id);
      if (!t) return;
      t.steps = t.steps || [];
      if (data.type === "agent_step") {
        t.partial = "";
        const existing = t.steps.find((s) => s.index === data.step);
        const step = existing || { index: data.step, content: "", tool_calls: [] };
        step.content = data.content || step.content;
        step.tool_calls = data.tool_calls || step.tool_calls;
        if (!existing) t.steps.push(step);
        const card = tasksEl.querySelector(`.task[data-id="${data.task_id}"]`);
        const live = card?.querySelector(".live");
        if (live) live.hidden = true;
      } else {
        const step = t.steps.find((s) => s.index === data.step);
        if (step) {
          step.tool_results = step.tool_results || [];
          step.tool_results.push({ name: data.name, result: data.result, error: data.error });
        }
      }
      renderTask(t);
    }
  };
  es.onerror = () => {
    statusDot.className = "dot err";
    statusText.textContent = "mất kết nối, đang thử lại…";
  };
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  if (state.uploading) {
    formError.hidden = false;
    formError.textContent = "đang upload file, đợi chút…";
    return;
  }
  submitBtn.disabled = true;
  formError.hidden = true;
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        title: $("#title").value.trim(),
        prompt,
        agent: $("#agent").value.trim() || null,
        model: modelSelect.value || null,
        auto_run: $("#autoRun").checked,
        attachments: state.attachments.map((f) => f.path),
      }),
    });
    promptEl.value = "";
    $("#title").value = "";
    state.attachments = [];
    renderAttachments();
    await loadTasks();
    refreshHealth();
  } catch (err) {
    formError.hidden = false;
    formError.textContent = err.message;
  } finally {
    submitBtn.disabled = false;
  }
});

promptEl.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") form.requestSubmit();
});

$("#filters").addEventListener("click", async (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
  state.filter = chip.dataset.status;
  await loadTasks();
});

(async function init() {
  renderAttachments();
  await refreshHealth();
  await loadModels();
  await loadTasks();
  connectEvents();
  setInterval(refreshHealth, 10000);
})();
