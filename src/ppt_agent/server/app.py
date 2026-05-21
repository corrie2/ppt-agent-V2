from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from ppt_agent.app.web_service import PptAgentWebService


class CreateSessionRequest(BaseModel):
    cwd: str | None = None
    assistant_enabled: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class GeneratePlanRequest(BaseModel):
    topic: str | None = None
    sources: list[str] | None = None
    audience: str | None = None
    tone: str | None = None
    slides: int | None = Field(default=None, ge=1)
    min_slides: int | None = Field(default=None, ge=1)
    output_format: str | None = None
    output_name: str | None = None
    applied_skills: list[str] | None = None
    theme: str | None = None

    def arguments(self) -> dict[str, Any]:
        return {key: value for key, value in self.model_dump(mode="json").items() if value not in (None, [], "")}


def create_app() -> FastAPI:
    app = FastAPI(title="PPT Agent Studio", version="0.1.0")
    service = PptAgentWebService()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.post("/api/sessions")
    def create_session(request: CreateSessionRequest) -> dict[str, Any]:
        return service.create_session(cwd=request.cwd, assistant_enabled=request.assistant_enabled)

    @app.get("/api/sessions/{session_id}/state")
    def get_state(session_id: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.state(session_id))

    @app.post("/api/sessions/{session_id}/workspace/scan")
    def scan_workspace(session_id: str, max_depth: int = 3) -> dict[str, Any]:
        return _get_or_404(lambda: service.scan_workspace(session_id, max_depth=max_depth))

    @app.post("/api/sessions/{session_id}/chat")
    def chat(session_id: str, request: ChatRequest) -> dict[str, Any]:
        return _get_or_404(lambda: service.chat(session_id, request.message))

    @app.post("/api/sessions/{session_id}/plans")
    def generate_plan(session_id: str, request: GeneratePlanRequest) -> dict[str, Any]:
        return _get_or_404(lambda: service.generate_plan(session_id, request.arguments()))

    @app.get("/api/sessions/{session_id}/plans/latest")
    def latest_plan(session_id: str) -> dict[str, Any]:
        result = _get_or_404(lambda: service.latest_plan(session_id))
        if result is None:
            raise HTTPException(status_code=404, detail="No latest plan.")
        return result

    @app.post("/api/sessions/{session_id}/builds/approve")
    def approve_build(session_id: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.approve_build(session_id))

    @app.get("/api/sessions/{session_id}/skills")
    def skills(session_id: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.skills(session_id))

    @app.post("/api/sessions/{session_id}/skills/{skill_name}/enable")
    def enable_skill(session_id: str, skill_name: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.set_skill_disabled(session_id, skill_name, disabled=False))

    @app.post("/api/sessions/{session_id}/skills/{skill_name}/disable")
    def disable_skill(session_id: str, skill_name: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.set_skill_disabled(session_id, skill_name, disabled=True))

    @app.get("/api/sessions/{session_id}/artifacts")
    def artifacts(session_id: str) -> list[dict[str, Any]]:
        return _get_or_404(lambda: service.artifacts(session_id))

    @app.get("/api/sessions/{session_id}/artifact")
    def artifact(session_id: str, path: str = Query(...)) -> FileResponse:
        state = _get_or_404(lambda: service.state(session_id))
        resolved = Path(path).resolve()
        allowed_roots = [Path(state["input_dir"]).resolve(), Path(state["output_dir"]).resolve(), Path(state["cwd"]).resolve() / ".ppt-agent"]
        if not any(_is_relative_to(resolved, root) for root in allowed_roots):
            raise HTTPException(status_code=403, detail="Artifact path is outside the session workspace.")
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(resolved)

    return app


def _get_or_404(fn):
    try:
        return fn()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


app = create_app()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PPT Agent Studio</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f1ea;
      --page: #fbfaf7;
      --panel: #fffdfa;
      --panel-soft: #f7f4ee;
      --line: #ded6ca;
      --line-strong: #cabdaf;
      --text: #241f1a;
      --muted: #74695e;
      --subtle: #9a8e82;
      --accent: #7b3f2a;
      --accent-2: #326b5b;
      --accent-soft: #efe4dc;
      --good-soft: #e6eee8;
      --danger: #9f2f26;
      --mono: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      --sans: Inter, ui-sans-serif, Segoe UI, Arial, sans-serif;
      --serif: Georgia, "Times New Roman", serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.45;
    }
    button, input, textarea, select { font: inherit; }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 999px;
      padding: 9px 13px;
      cursor: pointer;
      transition: background .15s ease, border-color .15s ease, color .15s ease, transform .15s ease;
    }
    button:hover:not(:disabled) { border-color: var(--line-strong); background: #f7f1e8; }
    button:active:not(:disabled) { transform: translateY(1px); }
    button.primary { background: var(--text); border-color: var(--text); color: var(--page); }
    button.primary:hover:not(:disabled) { background: #3a3128; border-color: #3a3128; }
    button.good { background: var(--accent-2); border-color: var(--accent-2); color: #fffdfa; }
    button.good:hover:not(:disabled) { background: #285849; border-color: #285849; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    header {
      min-height: 76px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 18px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--page);
    }
    header h1 {
      font-family: var(--serif);
      font-size: 34px;
      line-height: 1;
      margin: 0;
      font-weight: 500;
      letter-spacing: 0;
    }
    .brand {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
    .eyebrow {
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    header .meta {
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
      max-width: 56vw;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 11px;
      background: var(--panel-soft);
    }
    .layout {
      display: grid;
      grid-template-columns: 292px minmax(460px, 1fr) 336px;
      grid-template-rows: minmax(0, 1fr) 300px;
      min-height: calc(100vh - 76px);
      gap: 14px;
      padding: 16px;
      background:
        linear-gradient(90deg, rgba(36, 31, 26, .035) 1px, transparent 1px),
        var(--bg);
      background-size: 46px 46px;
    }
    .panel {
      min-width: 0;
      min-height: 0;
      background: var(--panel);
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .panel h2 {
      margin: 0;
      padding: 16px 16px 12px;
      font-size: 12px;
      color: var(--muted);
      letter-spacing: .08em;
      text-transform: uppercase;
      border-bottom: 1px solid var(--line);
      background: rgba(247, 244, 238, .72);
    }
    .panel-body { padding: 14px 16px; }
    .left { grid-row: 1 / 3; }
    .main { display: flex; flex-direction: column; }
    .right { grid-column: 3; grid-row: 1 / 3; }
    .bottom { grid-column: 2; }
    .stack { display: flex; flex-direction: column; gap: 12px; }
    .row { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; }
    .field { display: flex; flex-direction: column; gap: 7px; }
    label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      letter-spacing: .02em;
    }
    textarea, input, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: var(--page);
      color: var(--text);
    }
    textarea:focus, input:focus, select:focus {
      outline: 2px solid rgba(123, 63, 42, .18);
      border-color: var(--accent);
    }
    textarea {
      min-height: 128px;
      resize: vertical;
      font-size: 15px;
    }
    .events {
      flex: 1;
      min-height: 180px;
      overflow: auto;
      border-top: 1px solid var(--line);
      padding: 16px;
      background: #27231f;
      color: #eee8dc;
      font-family: var(--mono);
      font-size: 12px;
      white-space: pre-wrap;
    }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px;
      margin-bottom: 10px;
      background: var(--page);
    }
    .item strong {
      display: block;
      font-size: 14px;
      font-weight: 650;
      overflow-wrap: anywhere;
      margin-bottom: 5px;
    }
    .item small {
      color: var(--muted);
      font-family: var(--mono);
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      color: var(--muted);
      margin: 0 6px 6px 0;
      background: var(--page);
    }
    .pill.active { color: var(--accent-2); border-color: #a6bbae; background: var(--good-soft); }
    .pill.disabled { color: var(--danger); border-color: #d9aaa1; background: #f8e8e4; }
    .skill-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px;
      background: var(--page);
      margin-bottom: 10px;
    }
    .skill-card.disabled { opacity: .72; background: #f8eee9; }
    .skill-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
    }
    .skill-name {
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .skill-desc {
      color: var(--muted);
      font-size: 12px;
      margin-top: 6px;
      overflow-wrap: anywhere;
    }
    .skill-meta {
      color: var(--subtle);
      font-family: var(--mono);
      font-size: 11px;
      margin-top: 7px;
      overflow-wrap: anywhere;
    }
    .agent-group {
      border-top: 1px solid var(--line);
      padding: 10px 0;
    }
    .agent-group:first-child { border-top: 0; padding-top: 0; }
    .agent-name {
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    .agent-empty {
      color: var(--subtle);
      font-size: 12px;
    }
    pre {
      margin: 0;
      padding: 16px;
      overflow: auto;
      height: 100%;
      background: #211d19;
      color: #f2eadf;
      font-family: var(--mono);
      font-size: 12px;
    }
    .preview-tabs {
      display: flex;
      gap: 8px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-soft);
    }
    .preview-tabs button { border-radius: 999px; padding: 7px 12px; }
    .preview-tabs button.active {
      color: var(--page);
      background: var(--accent);
      border-color: var(--accent);
      font-weight: 650;
    }
    .artifact-link { color: var(--accent); text-decoration: none; }
    .artifact-link:hover { text-decoration: underline; }
    .section-label {
      margin: 8px 0 0;
      padding: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    @media (max-width: 980px) {
      header { align-items: flex-start; flex-direction: column; }
      header .meta { max-width: 100%; white-space: normal; }
      .layout { grid-template-columns: 1fr; grid-template-rows: auto auto auto auto; height: auto; min-height: 0; }
      .left, .right, .bottom { grid-column: 1; grid-row: auto; }
      .panel { min-height: 260px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="eyebrow">Local presentation workbench</div>
      <h1>PPT Agent Studio</h1>
    </div>
    <div class="meta" id="sessionMeta">starting...</div>
  </header>
  <div class="layout">
    <aside class="panel left">
      <h2>Workspace</h2>
      <div class="panel-body stack">
        <button class="primary" id="scanBtn">Scan Workspace</button>
        <div id="files"></div>
        <div class="section-label">Skills</div>
        <div id="skills"></div>
        <div class="section-label">Agent Skill Routing</div>
        <div id="agentSkills"></div>
      </div>
    </aside>
    <main class="panel main">
      <h2>Request</h2>
      <div class="panel-body stack">
        <div class="field">
          <label for="message">Natural language request</label>
          <textarea id="message" placeholder="Example: Use SIEVE.pdf to make a 20+ slide graduate teaching deck in magazine-style HTML."></textarea>
        </div>
        <div class="row">
          <button class="primary" id="sendBtn">Send</button>
          <button id="planBtn">Generate Plan</button>
          <button class="good" id="buildBtn" disabled>Approve Build</button>
        </div>
      </div>
      <div class="events" id="events"></div>
    </main>
    <aside class="panel right">
      <h2>Session State</h2>
      <div class="panel-body stack">
        <div id="draft"></div>
        <div id="pipeline"></div>
        <div id="artifacts"></div>
      </div>
    </aside>
    <section class="panel bottom">
      <div class="preview-tabs">
        <button class="active" id="planTab">Plan JSON</button>
        <button id="stateTab">State JSON</button>
      </div>
      <pre id="preview">No plan yet.</pre>
    </section>
  </div>
  <script>
    let state = null;
    let activePreview = "plan";

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    }

    async function start() {
      state = await api("/api/sessions", { method: "POST", body: JSON.stringify({ assistant_enabled: true }) });
      render();
    }

    async function refresh() {
      state = await api(`/api/sessions/${state.session_id}/state`);
      render();
    }

    async function loadPlan() {
      if (!state || !state.latest_plan_path) return null;
      try {
        return await api(`/api/sessions/${state.session_id}/plans/latest`);
      } catch {
        return null;
      }
    }

    function render() {
      document.getElementById("sessionMeta").textContent =
        `${state.assistant_provider || "none"}/${state.assistant_model || "none"} | ${state.cwd}`;
      document.getElementById("buildBtn").disabled = !state.pending_action;
      renderFiles();
      renderSkills();
      renderDraft();
      renderPipeline();
      renderArtifacts();
      renderEvents();
      renderPreview();
    }

    function renderFiles() {
      const files = state.files || [];
      document.getElementById("files").innerHTML = files.length ? files.map(file => `
        <div class="item">
          <strong>${escapeHtml(file.name)}</strong>
          <small>${escapeHtml(file.file_type || "")} | ${escapeHtml(file.path || "")}</small>
        </div>
      `).join("") : `<div class="item"><small>No files scanned yet.</small></div>`;
    }

    function renderSkills() {
      const catalog = state.skill_catalog || [];
      document.getElementById("skills").innerHTML = catalog.length ? catalog.map(skill => {
        const canToggle = (state.available_user_skills || []).includes(skill.name);
        const disabled = !!skill.session_disabled || !skill.enabled;
        const status = disabled ? "disabled" : "enabled";
        const builtins = (skill.allowed_builtin_skills || []).slice(0, 4).join(", ");
        return `
          <div class="skill-card ${disabled ? "disabled" : ""}">
            <div class="skill-head">
              <div class="skill-name">${escapeHtml(skill.name)}</div>
              ${canToggle
                ? `<button class="pill ${disabled ? "disabled" : "active"}" data-skill="${escapeAttr(skill.name)}">${status}</button>`
                : `<span class="pill disabled">unavailable</span>`}
            </div>
            <div class="skill-desc">${escapeHtml(skill.description || skill.when_to_use || "No description provided.")}</div>
            <div class="skill-meta">${escapeHtml(skill.source || "unknown")} | tools: ${escapeHtml(builtins || "none")}</div>
          </div>`;
      }).join("") : `<span class="pill">none</span>`;
      renderAgentSkills();
      document.querySelectorAll("[data-skill]").forEach(button => {
        button.addEventListener("click", async () => {
          const name = button.getAttribute("data-skill");
          const disabled = (state.disabled_user_skills || []).includes(name);
          await api(`/api/sessions/${state.session_id}/skills/${encodeURIComponent(name)}/${disabled ? "enable" : "disable"}`, { method: "POST" });
          await refresh();
        });
      });
    }

    function renderAgentSkills() {
      const assignments = state.agent_skill_assignments || {};
      const preferredOrder = [
        "supervisor",
        "brief_outline",
        "content",
        "design_chart",
        "qa",
        "page_designer",
        "renderer_engineer",
        "page_generator",
        "render_review",
        "visual_quality_evaluator"
      ];
      const agents = preferredOrder.filter(agent => Object.prototype.hasOwnProperty.call(assignments, agent))
        .concat(Object.keys(assignments).filter(agent => !preferredOrder.includes(agent)).sort());
      document.getElementById("agentSkills").innerHTML = agents.length ? agents.map(agent => {
        const skills = assignments[agent] || [];
        return `
          <div class="agent-group">
            <div class="agent-name">${escapeHtml(agent)}</div>
            ${skills.length ? skills.map(skill => `<span class="pill active" title="${escapeAttr(skill.description || "")}">${escapeHtml(skill.name)}</span>`).join("") : `<div class="agent-empty">No active skills assigned.</div>`}
          </div>`;
      }).join("") : `<div class="agent-empty">No agent routing available.</div>`;
    }

    function renderDraft() {
      const draft = state.draft_request || {};
      const rows = [
        ["Topic", draft.topic],
        ["Audience", draft.audience],
        ["Slides", draft.slides || draft.min_slides],
        ["Format", draft.output_format],
        ["Output", draft.output_name],
        ["Skills", (draft.applied_skills || []).join(", ")]
      ];
      document.getElementById("draft").innerHTML = `<div class="item"><strong>Draft Request</strong>${
        rows.map(([k, v]) => `<small>${k}: ${escapeHtml(v || "none")}</small>`).join("<br>")
      }</div>`;
    }

    function renderPipeline() {
      const pending = state.pending_action ? state.pending_action.description : "none";
      document.getElementById("pipeline").innerHTML = `
        <div class="item">
          <strong>Pipeline</strong>
          <small>plan: ${escapeHtml(state.latest_plan_path || "none")}</small><br>
          <small>evidence: ${escapeHtml(state.latest_evidence_path || "none")}</small><br>
          <small>build: ${escapeHtml(state.last_build_status || "none")}</small><br>
          <small>pending: ${escapeHtml(pending)}</small>
        </div>`;
    }

    function renderArtifacts() {
      const artifacts = state.artifacts || [];
      document.getElementById("artifacts").innerHTML = `<div class="item"><strong>Artifacts</strong>${
        artifacts.length ? artifacts.map(item => {
          const href = `/api/sessions/${state.session_id}/artifact?path=${encodeURIComponent(item.path)}`;
          return `<br><a class="artifact-link" target="_blank" href="${href}">${escapeHtml(item.name)}</a>`;
        }).join("") : "<br><small>none</small>"
      }</div>`;
    }

    function renderEvents() {
      document.getElementById("events").textContent = (state.events || []).join("\n") || "No events yet.";
    }

    async function renderPreview() {
      const preview = document.getElementById("preview");
      if (activePreview === "state") {
        preview.textContent = JSON.stringify(state, null, 2);
        return;
      }
      const plan = await loadPlan();
      preview.textContent = plan ? JSON.stringify(plan.payload, null, 2) : "No plan yet.";
    }

    function setPreview(tab) {
      activePreview = tab;
      document.getElementById("planTab").classList.toggle("active", tab === "plan");
      document.getElementById("stateTab").classList.toggle("active", tab === "state");
      renderPreview();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }

    document.getElementById("scanBtn").addEventListener("click", async () => {
      const result = await api(`/api/sessions/${state.session_id}/workspace/scan`, { method: "POST" });
      state = result.state;
      render();
    });
    document.getElementById("sendBtn").addEventListener("click", async () => {
      const message = document.getElementById("message").value.trim();
      if (!message) return;
      const result = await api(`/api/sessions/${state.session_id}/chat`, { method: "POST", body: JSON.stringify({ message }) });
      state = result.state;
      render();
    });
    document.getElementById("planBtn").addEventListener("click", async () => {
      const draft = state.draft_request || {};
      const result = await api(`/api/sessions/${state.session_id}/plans`, { method: "POST", body: JSON.stringify(draft) });
      state = result.state;
      render();
    });
    document.getElementById("buildBtn").addEventListener("click", async () => {
      const result = await api(`/api/sessions/${state.session_id}/builds/approve`, { method: "POST" });
      state = result.state;
      render();
    });
    document.getElementById("planTab").addEventListener("click", () => setPreview("plan"));
    document.getElementById("stateTab").addEventListener("click", () => setPreview("state"));
    start().catch(error => {
      document.getElementById("sessionMeta").textContent = "failed";
      document.getElementById("events").textContent = error.message;
    });
  </script>
</body>
</html>"""
