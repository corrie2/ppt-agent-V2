from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ppt_agent.app.web_service import PptAgentWebService

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


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


class ParsePdfRequest(BaseModel):
    pdf_path: str


class FigureSelectRequest(BaseModel):
    figure_ids: list[str] = Field(default_factory=list)


class TaskApprovalRequest(BaseModel):
    stage: str = "plan_confirm"
    note: str = "approved"


class TaskContinueRequest(BaseModel):
    auto_rework: bool = False
    max_rework: int = Field(default=1, ge=0, le=5)


class TaskRejectRequest(BaseModel):
    stage: str = "plan_confirm"
    reason: str = Field(default="changes requested", min_length=1)


class TaskGateRequest(BaseModel):
    stage: str = "content"


def create_app() -> FastAPI:
    import logging
    import os

    _api_key = os.environ.get("PPT_AGENT_API_KEY", "")

    app = FastAPI(title="PPT Agent Studio", version="0.1.0")
    service = PptAgentWebService()
    logger = logging.getLogger(__name__)

    @app.on_event("startup")
    def _startup_mineru_api() -> None:
        if os.environ.get("PPT_AGENT_PRELOAD_MINERU", "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        from ppt_agent.ingest.mineru_adapter import detect_mineru_device, ensure_mineru_api_server

        device, reason = detect_mineru_device()
        host = os.environ.get("PPT_AGENT_MINERU_HOST", "127.0.0.1")
        port = int(os.environ.get("PPT_AGENT_MINERU_PORT", "8000"))
        timeout = int(os.environ.get("PPT_AGENT_MINERU_PRELOAD_TIMEOUT", "180"))
        enable_vlm_preload = os.environ.get("PPT_AGENT_MINERU_VLM_PRELOAD", "1").strip().lower() in {"1", "true", "yes", "on"}
        logger.info("Starting MinerU API preload: device=%s (%s), host=%s, port=%s", device, reason, host, port)
        try:
            api_url, status = ensure_mineru_api_server(
                host=host,
                port=port,
                device=device,
                enable_vlm_preload=enable_vlm_preload,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            logger.error("MinerU API preload failed: %s", exc, exc_info=True)
            return
        os.environ["PPT_AGENT_MINERU_API_URL"] = api_url
        logger.info("MinerU API preload ready: %s (%s)", api_url, status)

    if _api_key:
        @app.middleware("http")
        async def _auth_middleware(request: Request, call_next):
            # Allow the root HTML page without auth
            if request.url.path == "/":
                return await call_next(request)
            header_key = request.headers.get("x-api-key", "")
            auth_header = request.headers.get("authorization", "")
            bearer = ""
            if auth_header.lower().startswith("bearer "):
                bearer = auth_header[7:]
            if header_key != _api_key and bearer != _api_key:
                raise HTTPException(status_code=401, detail="Missing or invalid API key.")
            return await call_next(request)
        logging.getLogger(__name__).info("API key authentication ENABLED (PPT_AGENT_API_KEY is set)")
    else:
        logging.getLogger(__name__).warning("API key authentication DISABLED (set PPT_AGENT_API_KEY to enable)")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.post("/api/sessions")
    def create_session(request: CreateSessionRequest) -> dict[str, Any]:
        return service.create_session(cwd=request.cwd, assistant_enabled=request.assistant_enabled)

    @app.get("/api/sessions/{session_id}/exists")
    def session_exists(session_id: str) -> dict[str, Any]:
        """Check if a session exists (for reconnect)."""
        exists = service.sessions.has(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Session not found or expired.")
        return {"exists": True}

    @app.get("/api/sessions/{session_id}/state")
    def get_state(session_id: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.state(session_id))

    @app.get("/api/sessions/{session_id}/status")
    def get_status(session_id: str) -> dict[str, Any]:
        """Check if background task is running and get latest events."""
        def _get_status():
            ws = service.sessions.get(session_id)
            with ws._lock:
                busy = ws.background_task is not None and ws.background_task.is_alive()
                events_snapshot = list(ws.events)[-20:]
                last_err = ws.last_error
            total = len(ws.events)
            offset = max(0, total - 20)
            return {
                "busy": busy,
                "events": events_snapshot,
                "event_offset": offset,
                "last_error": last_err,
                "state": service.state(session_id),
            }
        return _get_or_404(_get_status)

    @app.post("/api/sessions/{session_id}/workspace/scan")
    def scan_workspace(session_id: str, max_depth: int = 3) -> dict[str, Any]:
        return _get_or_404(lambda: service.scan_workspace(session_id, max_depth=max_depth))

    @app.post("/api/sessions/{session_id}/upload")
    async def upload_files(session_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        """Upload files (PDF, DOCX, MD, TXT) to the session workspace."""
        file_data = []
        for f in files:
            content_length = f.size  # may be None
            if content_length is not None and content_length > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds 100 MB limit.")
            data = await f.read()
            if len(data) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds 100 MB limit.")
            file_data.append((f.filename or "unnamed", data))
        return _get_or_404(lambda: service.upload_files(session_id, file_data))

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

    @app.get("/api/sessions/{session_id}/tasks")
    def harness_tasks(session_id: str) -> list[dict[str, Any]]:
        return _get_or_404(lambda: service.harness_tasks(session_id))

    @app.get("/api/sessions/{session_id}/tasks/{task_id}")
    def harness_task(session_id: str, task_id: str) -> dict[str, Any]:
        return _get_or_404(lambda: service.harness_task(session_id, task_id))

    @app.get("/api/sessions/{session_id}/tasks/{task_id}/events")
    def harness_task_events(session_id: str, task_id: str, limit: int = Query(default=50, ge=1, le=500)) -> list[dict[str, Any]]:
        return _get_or_404(lambda: service.harness_task_events(session_id, task_id, limit=limit))

    @app.get("/api/sessions/{session_id}/tasks/{task_id}/artifacts")
    def harness_task_artifacts(session_id: str, task_id: str) -> list[dict[str, Any]]:
        return _get_or_404(lambda: service.harness_task_artifacts(session_id, task_id))

    @app.post("/api/sessions/{session_id}/tasks/{task_id}/continue")
    def harness_task_continue(session_id: str, task_id: str, request: TaskContinueRequest = TaskContinueRequest()) -> dict[str, Any]:
        return _get_or_404(lambda: service.harness_task_continue(session_id, task_id, auto_rework=request.auto_rework, max_rework=request.max_rework))

    @app.post("/api/sessions/{session_id}/tasks/{task_id}/approve")
    def harness_task_approve(session_id: str, task_id: str, request: TaskApprovalRequest) -> dict[str, Any]:
        return _get_or_404(lambda: service.harness_task_approve(session_id, task_id, stage=request.stage, note=request.note))

    @app.post("/api/sessions/{session_id}/tasks/{task_id}/reject")
    def harness_task_reject(session_id: str, task_id: str, request: TaskRejectRequest) -> dict[str, Any]:
        return _get_or_404(lambda: service.harness_task_reject(session_id, task_id, stage=request.stage, reason=request.reason))

    @app.post("/api/sessions/{session_id}/tasks/{task_id}/gates")
    def harness_task_gates(session_id: str, task_id: str, request: TaskGateRequest) -> dict[str, Any]:
        return _get_or_404(lambda: service.harness_task_gates(session_id, task_id, stage=request.stage))

    @app.get("/api/sessions/{session_id}/artifact")
    def artifact(session_id: str, path: str = Query(...)) -> FileResponse:
        state = _get_or_404(lambda: service.state(session_id))
        resolved = Path(path).resolve()
        workspace_dir = Path(state["workspace_dir"]).resolve()
        # Restrict to workspace_dir subtree only
        if not _is_relative_to(resolved, workspace_dir):
            raise HTTPException(status_code=403, detail="Artifact path is outside the session workspace.")
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(resolved, filename=resolved.name)

    # ── Evidence Figure Selection ──

    @app.post("/api/sessions/{session_id}/parse-pdf")
    def parse_pdf(session_id: str, request: ParsePdfRequest) -> dict[str, Any]:
        """Parse a PDF with MinerU to extract text and figures."""
        return _get_or_404(lambda: service.parse_pdf(session_id, request.pdf_path))

    @app.get("/api/sessions/{session_id}/evidence/figures")
    def list_evidence_figures(session_id: str) -> dict[str, Any]:
        """List all figures extracted by MinerU from the evidence pack."""
        return _get_or_404(lambda: service.list_evidence_figures(session_id))

    @app.post("/api/sessions/{session_id}/evidence/figures/select")
    def select_evidence_figures(session_id: str, request: FigureSelectRequest) -> dict[str, Any]:
        """User selects which figures to use in the PPT."""
        return _get_or_404(lambda: service.select_evidence_figures(session_id, request.figure_ids))

    @app.get("/api/sessions/{session_id}/evidence/figures/{figure_id}/image")
    def get_figure_image(session_id: str, figure_id: str) -> FileResponse:
        """Return the image file for a specific figure."""
        resolved = _get_or_404(lambda: service.get_figure_image(session_id, figure_id))
        return FileResponse(resolved, filename=resolved.name)

    @app.get("/api/sessions/{session_id}/evidence/sources")
    def list_evidence_sources(session_id: str) -> dict[str, Any]:
        """List all sources and their figures/tables grouped by PDF."""
        return _get_or_404(lambda: service.list_evidence_sources(session_id))

    @app.delete("/api/sessions/{session_id}/evidence/figures/{figure_id}")
    def delete_evidence_figure(session_id: str, figure_id: str) -> dict[str, Any]:
        """Delete a single figure from evidence."""
        return _get_or_404(lambda: service.delete_evidence_figure(session_id, figure_id))

    @app.delete("/api/sessions/{session_id}/evidence/sources/{source_file}")
    def delete_evidence_source(session_id: str, source_file: str) -> dict[str, Any]:
        """Delete all figures/tables from a specific source PDF."""
        return _get_or_404(lambda: service.delete_evidence_source(session_id, source_file))

    return app


def _get_or_404(fn):
    try:
        return fn()
    except KeyError as exc:
        import logging
        logging.getLogger(__name__).warning("Resource not found: %s", exc)
        raise HTTPException(status_code=404, detail="Resource not found.") from exc
    except (ValueError, ValidationError) as exc:
        import logging
        logging.getLogger(__name__).warning("Validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid request data.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Server error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


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
      height: 100vh;
      overflow: hidden;
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
      height: calc(100vh - 76px);
      overflow: hidden;
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
      background: #f1eee8;
      color: #2f2a24;
      font-family: var(--mono);
      font-size: 12px;
      white-space: pre-wrap;
    }
    
    .event-progress {
      color: #315f70;
      padding: 4px 0;
      border-left: 3px solid #6f9fb0;
      padding-left: 8px;
      margin: 4px 0;
      background: rgba(111, 159, 176, .08);
    }
    
    .agent-planner {
      color: #7b5b19;
      border-left-color: #c9a24e;
    }
    
    .agent-content {
      color: #2f6b4f;
      border-left-color: #6aa986;
    }
    
    .agent-design {
      color: #67558a;
      border-left-color: #9b88bf;
    }
    
    .agent-qa {
      color: #8b3f36;
      border-left-color: #c07165;
    }
    
    .agent-builder {
      color: #6f5b1b;
      border-left-color: #b99d48;
    }
    
    .event-user {
      color: #245d47;
      padding: 4px 0;
      font-weight: bold;
    }
    
    .event-agent {
      color: #704236;
      padding: 4px 0;
    }
    
    .event-default {
      color: #3a332b;
      padding: 4px 0;
    }
    
    .event-empty {
      color: var(--subtle);
      font-style: italic;
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
      background: #f1eee8;
      color: #2f2a24;
      font-family: var(--mono);
      font-size: 12px;
      border-top: 1px solid var(--line);
    }
    .preview-tabs {
      display: flex;
      gap: 8px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-soft);
      position: sticky;
      top: 0;
      z-index: 10;
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
    .artifact-group { margin-bottom: 6px; }
    .artifact-group-title { font-size: 10px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; padding: 3px 0; border-bottom: 1px solid #e2e8f0; margin-bottom: 3px; user-select: none; }
    .artifact-row { display: flex; align-items: center; gap: 4px; padding: 2px 0; font-size: 11px; }
    .artifact-icon { font-size: 10px; flex-shrink: 0; }
    .artifact-link { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .artifact-meta { font-size: 9px; color: #94a3b8; white-space: nowrap; flex-shrink: 0; }
    .artifact-more { font-size: 9px; color: #94a3b8; padding: 2px 0; }
    /* Upload zone */
    .upload-zone { border: 2px dashed #cbd5e1; border-radius: 8px; padding: 16px 12px; text-align: center; cursor: pointer; transition: all 0.2s; background: #f8fafc; }
    .upload-zone:hover, .upload-zone.dragover { border-color: #3b82f6; background: #eff6ff; }
    .upload-icon { font-size: 24px; margin-bottom: 4px; }
    .upload-text { font-size: 12px; color: #64748b; }
    .upload-browse { color: #3b82f6; cursor: pointer; text-decoration: underline; }
    .upload-hint { font-size: 10px; color: #94a3b8; margin-top: 2px; }
    .upload-status { font-size: 11px; margin-top: 4px; }
    .upload-status.success { color: #10b981; }
    .upload-status.error { color: #ef4444; }
    .section-label {
      margin: 8px 0 0;
      padding: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      cursor: pointer;
      user-select: none;
    }
    .section-label:hover {
      color: var(--text);
    }
    .collapsible .section-content {
      overflow: hidden;
    }
    .collapsible.collapsed .section-content {
      display: none;
    }
    .collapsible.collapsed .section-label::before {
      content: "▶ ";
    }
    .collapsible:not(.collapsed) .section-label::before {
      content: "▼ ";
    }
    @media (max-width: 980px) {
      header { align-items: flex-start; flex-direction: column; }
      header .meta { max-width: 100%; white-space: normal; }
      .layout { grid-template-columns: 1fr; grid-template-rows: auto auto auto auto; height: auto; min-height: 0; }
      .left, .right, .bottom { grid-column: 1; grid-row: auto; }
      .panel { min-height: 260px; }
    }
    /* Figure selection grid */
    .figure-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 4px; max-height: calc(100vh - 200px); overflow-y: auto; }
    .figure-card { border: 2px solid #e2e8f0; border-radius: 6px; padding: 6px; cursor: pointer; transition: border-color 0.15s; text-align: center; }
    .figure-card:hover { border-color: #cbd5e0; }
    .figure-card.selected { border-color: #f59e0b; background: #fffbeb; }
    .figure-card img { width: 100%; height: 120px; object-fit: contain; border-radius: 3px; background: #f7f7f7; }
    .figure-card .fig-caption { font-size: 9px; color: #718096; margin-top: 4px; line-height: 1.3; max-height: 2.6em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .figure-card .fig-page { font-size: 8px; color: #a0aec0; margin-top: 2px; }
    .figure-actions { display: flex; gap: 4px; margin-bottom: 6px; }
    .figure-actions button { font-size: 10px; padding: 2px 6px; border-radius: 3px; border: 1px solid #e2e8f0; background: #fff; cursor: pointer; }
    .figure-actions button:hover { background: #f7fafc; }
    /* Material Library */
    .material-group { margin-bottom: 10px; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; background: #fff; }
    .material-group-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; background: #f7fafc; cursor: pointer; font-size: 11px; font-weight: 600; color: #4a5568; border-bottom: 1px solid #e2e8f0; user-select: none; }
    .material-group-header:hover { background: #edf2f7; }
    .material-group-header::before { content: '▸'; margin-right: 6px; font-size: 10px; transition: transform 0.15s; }
    .material-group.open .material-group-header::before { transform: rotate(90deg); }
    .material-group-header .count { font-size: 10px; color: #a0aec0; font-weight: 400; }
    .material-group-header .delete-btn { font-size: 9px; color: #e53e3e; cursor: pointer; padding: 2px 6px; border-radius: 3px; border: 1px solid #fed7d7; background: #fff5f5; }
    .material-group-header .delete-btn:hover { background: #fed7d7; }
    .material-group-body { display: none; padding: 8px; }
    .material-group.open .material-group-body { display: block; }
    .material-preview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .material-preview { border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; position: relative; cursor: pointer; transition: border-color 0.15s; }
    .material-preview:hover { border-color: #cbd5e0; }
    .material-preview img { width: 100%; height: 100px; object-fit: contain; background: #f7f7f7; display: block; }
    .material-preview .info { padding: 4px 6px; }
    .material-preview .info .caption { font-size: 9px; color: #4a5568; line-height: 1.3; max-height: 2.6em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
    .material-preview .info .meta { font-size: 8px; color: #a0aec0; margin-top: 2px; display: flex; justify-content: space-between; align-items: center; }
    .material-preview .del { position: absolute; top: 3px; right: 3px; background: rgba(255,255,255,0.9); border: 1px solid #fed7d7; color: #e53e3e; font-size: 10px; width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; opacity: 0; transition: opacity 0.15s; }
    .material-preview:hover .del { opacity: 1; }
    .material-preview .del:hover { background: #fed7d7; }
    .material-preview-placeholder { width: 100%; height: 100px; background: #f7fafc; display: flex; align-items: center; justify-content: center; font-size: 10px; color: #a0aec0; }
    /* Figure Selection grouped */
    .fig-group { margin-bottom: 6px; }
    .fig-group-header { display: flex; align-items: center; justify-content: space-between; padding: 3px 0; font-size: 10px; font-weight: 600; color: #4a5568; border-bottom: 1px solid #e2e8f0; margin-bottom: 4px; }
    .fig-group-header .group-actions { display: flex; gap: 3px; }
    .fig-group-header .group-actions button { font-size: 9px; padding: 1px 4px; border-radius: 3px; border: 1px solid #e2e8f0; background: #fff; cursor: pointer; }
    .fig-group-header .group-actions button:hover { background: #f7fafc; }
    .fig-check-grid { display: flex; flex-wrap: wrap; gap: 3px; }
    .fig-check { display: flex; align-items: center; gap: 3px; font-size: 9px; padding: 2px 4px; border-radius: 3px; cursor: pointer; border: 1px solid #e2e8f0; }
    .fig-check:hover { background: #f7fafc; }
    .fig-check.selected { border-color: #f59e0b; background: #fffbeb; }
    .fig-check.changed { border-color: #3b82f6; background: #eff6ff; box-shadow: 0 0 0 1px #3b82f6; }
    .fig-check input { margin: 0; }
    .figure-count { font-size: 10px; color: #718096; margin-bottom: 4px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>PPT Agent Studio</h1>
    </div>
    <div class="meta" id="sessionMeta">starting...</div>
  </header>
  <div class="layout">
    <aside class="panel left">
      <h2>Workspace</h2>
      <div class="panel-body stack">
        <button class="primary" id="scanBtn">Scan Workspace</button>
        <div class="upload-zone" id="uploadZone">
          <div class="upload-icon">📁</div>
          <div class="upload-text">Drop files here or <label for="fileInput" class="upload-browse">browse</label></div>
          <div class="upload-hint">PDF, DOCX, MD, TXT</div>
          <input type="file" id="fileInput" multiple accept=".pdf,.docx,.doc,.md,.txt,.markdown" style="display:none">
        </div>
        <button id="uploadBtn" disabled>Upload</button>
        <div id="uploadStatus" class="upload-status"></div>
        <div class="collapsible collapsed" data-section="materials">
          <div class="section-label" onclick="toggleSection(this)">Material Library <span id="materialCount" style="font-size:9px;color:#a0aec0;font-weight:400;"></span></div>
          <div class="section-content" id="materials"></div>
        </div>
        <div class="collapsible collapsed" data-section="files">
          <div class="section-label" onclick="toggleSection(this)">Scanned Files</div>
          <div class="section-content" id="files"></div>
        </div>
        <div class="collapsible collapsed" data-section="skills">
          <div class="section-label" onclick="toggleSection(this)">Skills</div>
          <div class="section-content" id="skills"></div>
        </div>
        <div class="collapsible collapsed" data-section="agentSkills">
          <div class="section-label" onclick="toggleSection(this)">Agent Skill Routing</div>
          <div class="section-content" id="agentSkills"></div>
        </div>
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
        <div class="collapsible collapsed" data-section="draft">
          <div class="section-label" onclick="toggleSection(this)">Draft Request</div>
          <div class="section-content" id="draft"></div>
        </div>
        <div class="collapsible collapsed" data-section="figures">
          <div class="section-label" onclick="toggleSection(this)">Figure Selection <span id="figSelCount" style="font-size:9px;color:#a0aec0;font-weight:400;"></span></div>
          <div class="section-content" id="figures"></div>
        </div>
        <div class="collapsible collapsed" data-section="pipeline">
          <div class="section-label" onclick="toggleSection(this)">Pipeline</div>
          <div class="section-content" id="pipeline"></div>
        </div>
        <div class="collapsible collapsed" data-section="artifacts">
          <div class="section-label" onclick="toggleSection(this)">Artifacts</div>
          <div class="section-content" id="artifacts"></div>
        </div>
      </div>
    </aside>
    <section class="panel bottom">
      <div class="preview-tabs">
        <button id="planTab">Plan JSON</button>
        <button id="stateTab">State JSON</button>
        <button class="active" id="thinkingTab">Thinking</button>
      </div>
      <pre id="preview">No plan yet.</pre>
    </section>
  </div>
  <script>
    let state = null;
    let activePreview = "thinking";
    let chatHistory = [];
    const MAX_HISTORY = 200;
    let isSending = false;  // 防止重复发送
    let lastEventIdx = 0;  // for index-based event dedup

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
      // Try to reuse existing session from localStorage
      const savedSessionId = localStorage.getItem("ppt_agent_session_id");
      if (savedSessionId) {
        try {
          state = await api(`/api/sessions/${savedSessionId}/state`);
          render();
          // Auto-scan workspace
          try {
            const scanResult = await api(`/api/sessions/${state.session_id}/workspace/scan`, { method: "POST" });
            state = scanResult.state;
            lastEventIdx = (state.events || []).length;
            render();
            loadMaterials();
          } catch (error) {
            console.error("Auto-scan failed:", error);
          }
          return;
        } catch (e) {
          // Session not found or expired, clear and create new
          localStorage.removeItem("ppt_agent_session_id");
        }
      }
      // Create new session
      state = await api("/api/sessions", { method: "POST", body: JSON.stringify({ assistant_enabled: true }) });
      localStorage.setItem("ppt_agent_session_id", state.session_id);
      render();
      
      // Auto-scan workspace on page load
      try {
        const scanResult = await api(`/api/sessions/${state.session_id}/workspace/scan`, { method: "POST" });
        state = scanResult.state;
        lastEventIdx = (state.events || []).length;
        render();
        loadMaterials();
      } catch (error) {
        console.error("Auto-scan failed:", error);
      }
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
        `${state.assistant_provider || "none"}/${state.assistant_model || "none"} | Workspace: ${state.workspace_dir || state.cwd}`;
      document.getElementById("buildBtn").disabled = !state.pending_action;
      renderFiles();
      renderSkills();
      renderDraft();
      renderPipeline();
      renderArtifacts();
      renderEvents();
      renderPreview();
      // Note: loadMaterials() is called explicitly, not on every render tick
    }

    function renderFiles() {
      const files = state.files || [];
      const filesContent = document.getElementById("files");
      
      if (files.length === 0) {
        filesContent.innerHTML = '<div class="item"><small>No files scanned yet.</small></div>';
        // Keep collapsed if no files
        return;
      }
      
      filesContent.innerHTML = files.map(file => {
        const isPdf = (file.file_type || '').toLowerCase().includes('pdf') || (file.name || '').toLowerCase().endsWith('.pdf');
        const parseBtn = isPdf ? ` <button class="parse-pdf-btn" data-pdf-path="${escapeAttr(file.path)}" style="font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid #e2e8f0;background:#fffbeb;cursor:pointer;color:#92400e;">Parse</button>` : '';
        return `
        <div class="item">
          <strong>${escapeHtml(file.name)}</strong>${parseBtn}
          <small>${escapeHtml(file.file_type || "")} | ${escapeHtml(file.path || "")}</small>
        </div>`;
      }).join("");

    }

    // Event delegation for parse buttons (avoids path escaping issues in onclick)
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.parse-pdf-btn');
      if (btn && btn.dataset.pdfPath) {
        e.preventDefault();
        parsePdf(btn.dataset.pdfPath);
      }
    });

    async function parsePdf(pdfPath) {
      chatHistory.push(`You: Parse PDF: ${pdfPath}`);
      renderEvents();
      try {
        const result = await api(`/api/sessions/${state.session_id}/parse-pdf`, {
          method: "POST",
          body: JSON.stringify({ pdf_path: pdfPath }),
        });
        state = result.state;
        if (result.message) chatHistory.push(`PPT Agent: ${result.message}`);
        render();
        // Start polling for background parse task
        if (result.ok) {
          lastEventIdx = Math.max(0, (state.events || []).length - 2);
          pollStatus();
        }
      } catch (error) {
        chatHistory.push(`PPT Agent: Parse error: ${error.message}`);
        render();
      }
    };

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
      const tasks = state.harness_tasks || [];
      const taskHtml = tasks.length ? tasks.slice(0, 3).map(task => {
        const total = task.total_stages || 0;
        const done = task.completed_stages || 0;
        const pct = total ? Math.round((done / total) * 100) : 0;
        const detailHref = `/api/sessions/${state.session_id}/tasks/${encodeURIComponent(task.task_id)}`;
        const waiting = task.status === "waiting_approval";
        const stage = task.current_stage || "plan_confirm";
        const controls = waiting ? `
            <div class="row" style="margin-top:8px">
              <button class="good harness-action" data-action="approve" data-task="${escapeAttr(task.task_id)}" data-stage="${escapeAttr(stage)}">Approve</button>
              <button class="harness-action" data-action="reject" data-task="${escapeAttr(task.task_id)}" data-stage="${escapeAttr(stage)}">Request changes</button>
            </div>` : `
            <div class="row" style="margin-top:8px">
              <button class="harness-action" data-action="continue" data-task="${escapeAttr(task.task_id)}">Continue</button>
            </div>`;
        return `
          <div class="item">
            <strong>${escapeHtml(task.title || task.topic || task.task_id)}</strong>
            <small>status: ${escapeHtml(task.status || "unknown")} | stages: ${done}/${total} (${pct}%)</small><br>
            <small>current: ${escapeHtml(task.current_stage || "none")}</small><br>
            <small><a class="artifact-link" target="_blank" href="${detailHref}">manifest</a></small>
            ${controls}
          </div>`;
      }).join("") : `<div class="item"><small>No Harness task runs yet.</small></div>`;
      document.getElementById("pipeline").innerHTML = `
        <div class="item">
          <strong>Pipeline</strong>
          <small>plan: ${escapeHtml(state.latest_plan_path || "none")}</small><br>
          <small>evidence: ${escapeHtml(state.latest_evidence_path || "none")}</small><br>
          <small>build: ${escapeHtml(state.last_build_status || "none")}</small><br>
          <small>pending: ${escapeHtml(pending)}</small>
        </div>
        ${taskHtml}`;
    }

    function renderArtifacts() {
      const artifacts = state.artifacts || [];
      const el = document.getElementById("artifacts");
      if (!artifacts.length) {
        el.innerHTML = '<div class="item"><small>No artifacts yet.</small></div>';
        return;
      }

      // Categorize
      const presentations = [];
      const plans = [];
      const reports = [];
      for (const item of artifacts) {
        const s = item.suffix;
        if (s === '.pptx' || s === '.html' || s === '.pdf') {
          presentations.push(item);
        } else if (s === '.json' && (item.name.includes('plan') || item.name.includes('evidence'))) {
          plans.push(item);
        } else {
          reports.push(item);
        }
      }

      function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
      }

      function formatTime(ts) {
        const diff = (Date.now() / 1000) - ts;
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
        if (diff < 86400) return Math.floor(diff / 3600) + ' hr ago';
        return Math.floor(diff / 86400) + ' day ago';
      }

      function renderGroup(items, icon, maxShow) {
        if (!items.length) return '';
        let html = '';
        const show = items.slice(0, maxShow || 10);
        for (const item of show) {
          const href = `/api/sessions/${state.session_id}/artifact?path=${encodeURIComponent(item.path)}`;
          const size = item.size ? formatSize(item.size) : '';
          const time = item.modified_time ? formatTime(item.modified_time) : '';
          html += `<div class="artifact-row">`;
          html += `<span class="artifact-icon">${icon}</span>`;
          html += `<a class="artifact-link" target="_blank" href="${href}" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</a>`;
          html += `<span class="artifact-meta">${size} · ${time}</span>`;
          html += `</div>`;
        }
        if (items.length > (maxShow || 10)) {
          html += `<div class="artifact-more">+${items.length - (maxShow || 10)} more</div>`;
        }
        return html;
      }

      let html = '';
      // Presentations (expanded)
      if (presentations.length) {
        html += `<div class="artifact-group">`;
        html += `<div class="artifact-group-title">Presentations (${presentations.length})</div>`;
        html += renderGroup(presentations, '📄', 10);
        html += `</div>`;
      }
      // Plans (collapsed)
      if (plans.length) {
        html += `<div class="artifact-group">`;
        html += `<div class="artifact-group-title" onclick="this.parentElement.classList.toggle('open')" style="cursor:pointer">Plans & Evidence (${plans.length}) ▸</div>`;
        html += `<div class="artifact-group-items" style="display:none">`;
        html += renderGroup(plans, '📋', 10);
        html += `</div></div>`;
      }
      // Reports (collapsed)
      if (reports.length) {
        html += `<div class="artifact-group">`;
        html += `<div class="artifact-group-title" onclick="this.parentElement.classList.toggle('open')" style="cursor:pointer">Reports (${reports.length}) ▸</div>`;
        html += `<div class="artifact-group-items" style="display:none">`;
        html += renderGroup(reports, '📊', 10);
        html += `</div></div>`;
      }

      el.innerHTML = html;

      // Toggle collapsed groups
      el.querySelectorAll('.artifact-group.open .artifact-group-items').forEach(i => i.style.display = '');
      el.querySelectorAll('.artifact-group:not(.open) .artifact-group-items').forEach(i => i.style.display = 'none');

      // Override toggle behavior
      el.querySelectorAll('.artifact-group-title[onclick]').forEach(title => {
        title.addEventListener('click', function(e) {
          e.stopPropagation();
          const group = this.parentElement;
          group.classList.toggle('open');
          const items = group.querySelector('.artifact-group-items');
          const isOpen = group.classList.contains('open');
          items.style.display = isOpen ? '' : 'none';
          this.textContent = this.textContent.replace(/[▸▾]/, isOpen ? '▾' : '▸');
        });
      });
    }

    // ── Figure Selection ──
    let figuresData = [];
    let selectedFigureIds = new Set();
    let confirmedFigureIds = new Set();  // last confirmed state
    let figuresDirty = false;  // has unconfirmed changes?

    async function loadFigures() {
      try {
        const data = await api(`/api/sessions/${state.session_id}/evidence/figures`);
        figuresData = data.figures || [];
        selectedFigureIds = new Set(data.selected_ids || []);
        confirmedFigureIds = new Set(data.selected_ids || []);
        figuresDirty = false;
        renderFigures();
      } catch (e) {
        figuresData = [];
        selectedFigureIds = new Set();
        confirmedFigureIds = new Set();
        figuresDirty = false;
        renderFigures();
      }
    }

    function renderFigures() {
      const el = document.getElementById("figures");
      const countEl = document.getElementById("figSelCount");
      if (!figuresData.length) {
        el.innerHTML = '<div class="item"><small>No figures extracted yet.</small></div>';
        if (countEl) countEl.textContent = '';
        return;
      }
      const selectedCount = selectedFigureIds.size;
      if (countEl) countEl.textContent = `(${selectedCount}/${figuresData.length})`;
      let html = '<div class="figure-actions">';
      html += '<button onclick="selectAllFigures()">Select All</button>';
      html += '<button onclick="deselectAllFigures()">Deselect All</button>';
      if (figuresDirty) {
        html += '<button onclick="confirmFigureSelection()" style="background:#10b981;color:#fff;font-weight:600;padding:2px 8px;border-radius:4px;border:none;cursor:pointer;margin-left:4px;">Confirm</button>';
        html += '<button onclick="revertFigureSelection()" style="background:#ef4444;color:#fff;font-size:11px;padding:2px 6px;border-radius:4px;border:none;cursor:pointer;margin-left:2px;">Revert</button>';
      }
      html += '</div>';
      // Group by source_file
      const groups = {};
      for (const fig of figuresData) {
        const src = fig.source_file || 'unknown';
        if (!groups[src]) groups[src] = [];
        groups[src].push(fig);
      }
      for (const [src, figs] of Object.entries(groups)) {
        html += `<div class="fig-group">`;
        html += `<div class="fig-group-header"><span>${escapeHtml(src)} (${figs.length})</span>`;
        html += `<span class="group-actions">`;
        html += `<button onclick="selectGroupFigures('${escapeAttr(src)}')">All</button>`;
        html += `<button onclick="deselectGroupFigures('${escapeAttr(src)}')">None</button>`;
        html += `</span></div>`;
        html += '<div class="fig-check-grid">';
        for (const fig of figs) {
          const isSelected = selectedFigureIds.has(fig.id);
          const isConfirmed = confirmedFigureIds.has(fig.id);
          const changed = isSelected !== isConfirmed;
          html += `<label class="fig-check ${isSelected ? 'selected' : ''} ${changed ? 'changed' : ''}">`;
          html += `<input type="checkbox" ${isSelected ? 'checked' : ''} onchange="toggleFigure('${escapeAttr(fig.id)}')">`;
          html += `<span title="${escapeHtml(fig.caption || '')}">${fig.id}</span>`;
          html += '</label>';
        }
        html += '</div></div>';
      }
      el.innerHTML = html;
    }

    window.toggleFigure = function(figId) {
      if (selectedFigureIds.has(figId)) {
        selectedFigureIds.delete(figId);
      } else {
        selectedFigureIds.add(figId);
      }
      figuresDirty = !setsEqual(selectedFigureIds, confirmedFigureIds);
      renderFigures();
      // No auto-sync — user must confirm
    };

    function setsEqual(a, b) {
      if (a.size !== b.size) return false;
      for (const x of a) if (!b.has(x)) return false;
      return true;
    }

    window.confirmFigureSelection = async function() {
      confirmedFigureIds = new Set(selectedFigureIds);
      figuresDirty = false;
      renderFigures();
      await syncFigureSelection();
    };

    window.revertFigureSelection = function() {
      selectedFigureIds = new Set(confirmedFigureIds);
      figuresDirty = false;
      renderFigures();
    };

    window.selectAllFigures = function() {
      selectedFigureIds = new Set(figuresData.map(f => f.id));
      figuresDirty = !setsEqual(selectedFigureIds, confirmedFigureIds);
      renderFigures();
    };

    window.deselectAllFigures = function() {
      selectedFigureIds = new Set();
      figuresDirty = !setsEqual(selectedFigureIds, confirmedFigureIds);
      renderFigures();
    };

    window.selectGroupFigures = function(sourceFile) {
      for (const fig of figuresData) {
        if ((fig.source_file || 'unknown') === sourceFile) {
          selectedFigureIds.add(fig.id);
        }
      }
      figuresDirty = !setsEqual(selectedFigureIds, confirmedFigureIds);
      renderFigures();
    };

    window.deselectGroupFigures = function(sourceFile) {
      for (const fig of figuresData) {
        if ((fig.source_file || 'unknown') === sourceFile) {
          selectedFigureIds.delete(fig.id);
        }
      }
      figuresDirty = !setsEqual(selectedFigureIds, confirmedFigureIds);
      renderFigures();
    };

    async function syncFigureSelection() {
      try {
        await api(`/api/sessions/${state.session_id}/evidence/figures/select`, {
          method: "POST",
          body: JSON.stringify({ figure_ids: Array.from(selectedFigureIds) }),
        });
      } catch (e) {
        console.error("Failed to sync figure selection:", e);
      }
    }

    // ── Material Library ──
    let materialData = [];
    let materialsLoading = false;

    async function loadMaterials() {
      if (materialsLoading) return;
      materialsLoading = true;
      try {
        const data = await api(`/api/sessions/${state.session_id}/evidence/sources`);
        materialData = data.sources || [];
        renderMaterials();
        loadFigures(); // Also refresh figure selection
      } catch (e) {
        materialData = [];
        renderMaterials();
      } finally {
        materialsLoading = false;
      }
    }

    function renderMaterials() {
      const el = document.getElementById("materials");
      const countEl = document.getElementById("materialCount");
      let totalItems = 0;
      for (const src of materialData) {
        totalItems += src.figures.length + src.tables.length;
      }
      if (countEl) {
        countEl.textContent = materialData.length > 0 ? `(${materialData.length} PDFs, ${totalItems} items)` : '';
      }
      if (!materialData.length) {
        el.innerHTML = '<div style="padding:12px;text-align:center;"><small style="color:#a0aec0;">No materials yet.<br>Click <b>Scan Workspace</b> then <b>Parse</b> a PDF.</small></div>';
        return;
      }
      let html = '';
      for (const src of materialData) {
        const total = src.figures.length + src.tables.length;
        html += `<div class="material-group">`;
        html += `<div class="material-group-header" onclick="this.parentElement.classList.toggle('open')">`;
        html += `<span>${escapeHtml(src.source_file)} <span class="count">(${total})</span></span>`;
        html += `<span class="delete-btn" onclick="event.stopPropagation();deleteSource('${escapeAttr(src.source_file)}')">Delete All</span>`;
        html += '</div>';
        html += '<div class="material-group-body"><div class="material-preview-grid">';
        for (const fig of src.figures) {
          const imgUrl = fig.has_image ? `/api/sessions/${state.session_id}/evidence/figures/${encodeURIComponent(fig.id)}/image` : '';
          html += `<div class="material-preview">`;
          html += `<span class="del" onclick="event.stopPropagation();deleteFigure('${escapeAttr(fig.id)}')" title="Delete">x</span>`;
          if (imgUrl) {
            html += `<img src="${imgUrl}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`;
            html += `<div class="material-preview-placeholder" style="display:none">[no image]</div>`;
          } else {
            html += `<div class="material-preview-placeholder">[no image]</div>`;
          }
          html += `<div class="info"><div class="caption" title="${escapeHtml(fig.caption)}">${escapeHtml(fig.caption || fig.id)}</div>`;
          html += `<div class="meta"><span>p.${fig.page || '?'}</span><span>${fig.id}</span></div></div>`;
          html += '</div>';
        }
        for (const tbl of src.tables) {
          const tblImgUrl = tbl.has_image ? `/api/sessions/${state.session_id}/evidence/figures/${encodeURIComponent(tbl.id)}/image` : '';
          html += `<div class="material-preview">`;
          html += `<span class="del" onclick="event.stopPropagation();deleteFigure('${escapeAttr(tbl.id)}')" title="Delete">x</span>`;
          if (tblImgUrl) {
            html += `<img src="${tblImgUrl}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`;
            html += `<div class="material-preview-placeholder" style="display:none;background:#f0fff4;color:#38a169;">TABLE</div>`;
          } else {
            html += `<div class="material-preview-placeholder" style="background:#f0fff4;color:#38a169;">TABLE</div>`;
          }
          html += `<div class="info"><div class="caption" title="${escapeHtml(tbl.caption)}">${escapeHtml(tbl.caption || tbl.id)}</div>`;
          html += `<div class="meta"><span>p.${tbl.page || '?'}</span><span>${tbl.id}</span></div></div>`;
          html += '</div>';
        }
        html += '</div></div></div>';
      }
      el.innerHTML = html;
    }

    window.deleteFigure = async function(figId) {
      if (!confirm('Delete this figure?')) return;
      try {
        await api(`/api/sessions/${state.session_id}/evidence/figures/${encodeURIComponent(figId)}`, { method: "DELETE" });
        selectedFigureIds.delete(figId);
        await loadMaterials();
      } catch (e) {
        console.error("Failed to delete figure:", e);
      }
    };

    window.deleteSource = async function(sourceFile) {
      if (!confirm(`Delete all materials from "${sourceFile}"?`)) return;
      try {
        await api(`/api/sessions/${state.session_id}/evidence/sources/${encodeURIComponent(sourceFile)}`, { method: "DELETE" });
        await loadMaterials();
      } catch (e) {
        console.error("Failed to delete source:", e);
      }
    };

    function renderEvents() {
      const events = document.getElementById("events");
      const history = chatHistory || [];
      
      if (history.length === 0) {
        events.innerHTML = '<div class="event-empty">No messages yet.</div>';
        return;
      }
      
      events.innerHTML = history.map(msg => {
        // Check if it's a progress message with agent name
        const agentMatch = msg.match(/^\[Progress\] \[(\w+)\] (.+)$/);
        if (agentMatch) {
          const agentName = agentMatch[1];
          const content = agentMatch[2];
          const agentClass = `agent-${agentName.toLowerCase()}`;
          return `<div class="event-progress ${agentClass}"><strong>[${agentName}]</strong> ${escapeHtml(content)}</div>`;
        }
        
        // Check if it's a progress message without agent name
        if (msg.startsWith('[Progress]')) {
          const content = msg.replace('[Progress] ', '');
          return `<div class="event-progress">${escapeHtml(content)}</div>`;
        }
        
        // Check if it's a user message
        if (msg.startsWith('You:')) {
          return `<div class="event-user">${escapeHtml(msg)}</div>`;
        }
        
        // Check if it's an agent message
        if (msg.startsWith('PPT Agent:')) {
          return `<div class="event-agent">${escapeHtml(msg)}</div>`;
        }
        
        // Default styling
        return `<div class="event-default">${escapeHtml(msg)}</div>`;
      }).join('');
      
      events.scrollTop = events.scrollHeight;
    }

    async function renderPreview() {
      const preview = document.getElementById("preview");
      if (activePreview === "state") {
        preview.textContent = JSON.stringify(state, null, 2);
        return;
      }
      if (activePreview === "thinking") {
        const thinking = state.last_thinking || "No thinking content yet.";
        preview.textContent = thinking
          .replace(/\. /g, '.\n')
          .replace(/。/g, '。\n')
          .replace(/\n\n+/g, '\n\n');
        return;
      }
      const plan = await loadPlan();
      preview.textContent = plan ? JSON.stringify(plan.payload, null, 2) : "No plan yet.";
    }

    function setPreview(tab) {
      activePreview = tab;
      document.getElementById("planTab").classList.toggle("active", tab === "plan");
      document.getElementById("stateTab").classList.toggle("active", tab === "state");
      document.getElementById("thinkingTab").classList.toggle("active", tab === "thinking");
      renderPreview();
    }

    function escapeHtml(value) {
      return String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }
    function escapeAttr(value) {
      return String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'\\"').replace(/\n/g,'\\n').replace(/\r/g,'\\r');
    }

    function toggleSection(label) {
      const collapsible = label.closest('.collapsible');
      if (collapsible) {
        collapsible.classList.toggle('collapsed');
      }
    }

    document.getElementById("scanBtn").addEventListener("click", async () => {
      try {
        const result = await api(`/api/sessions/${state.session_id}/workspace/scan`, { method: "POST" });
        state = result.state;
        render();
      } catch (error) {
        chatHistory.push(`PPT Agent: Scan error: ${error.message}`);
        render();
      }
    });

    // ── File Upload ──
    let pendingFiles = [];
    const uploadZone = document.getElementById("uploadZone");
    const fileInput = document.getElementById("fileInput");
    const uploadBtn = document.getElementById("uploadBtn");
    const uploadStatus = document.getElementById("uploadStatus");

    uploadZone.addEventListener("click", () => fileInput.click());
    uploadZone.addEventListener("dragover", (e) => { e.preventDefault(); uploadZone.classList.add("dragover"); });
    uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("dragover"));
    uploadZone.addEventListener("drop", (e) => {
      e.preventDefault();
      uploadZone.classList.remove("dragover");
      addPendingFiles(e.dataTransfer.files);
    });
    fileInput.addEventListener("change", () => addPendingFiles(fileInput.files));

    function addPendingFiles(fileList) {
      for (const f of fileList) {
        if (!pendingFiles.some(p => p.name === f.name)) {
          pendingFiles.push(f);
        }
      }
      updateUploadUI();
    }

    function updateUploadUI() {
      uploadBtn.disabled = pendingFiles.length === 0;
      if (pendingFiles.length) {
        uploadStatus.className = "upload-status";
        uploadStatus.textContent = `${pendingFiles.length} file(s) ready: ${pendingFiles.map(f => f.name).join(", ")}`;
      } else {
        uploadStatus.textContent = "";
      }
    }

    uploadBtn.addEventListener("click", async () => {
      if (!pendingFiles.length) return;
      uploadBtn.disabled = true;
      uploadStatus.className = "upload-status";
      uploadStatus.textContent = "Uploading...";

      try {
        const formData = new FormData();
        for (const f of pendingFiles) {
          formData.append("files", f);
        }
        const resp = await fetch(`/api/sessions/${state.session_id}/upload`, {
          method: "POST",
          body: formData,
        });
        const result = await resp.json();
        if (!resp.ok) throw new Error(result.detail || "Upload failed");

        const names = result.uploaded.map(f => f.name).join(", ");
        uploadStatus.className = "upload-status success";
        uploadStatus.textContent = `✓ Uploaded: ${names}`;
        if (result.errors.length) {
          uploadStatus.textContent += ` | Errors: ${result.errors.join(", ")}`;
        }
        chatHistory.push(`PPT Agent: Uploaded ${result.uploaded.length} file(s): ${names}`);
        pendingFiles = [];
        fileInput.value = "";
        state = result.state;
        render();
      } catch (error) {
        uploadStatus.className = "upload-status error";
        uploadStatus.textContent = `✗ ${error.message}`;
      }
      uploadBtn.disabled = false;
    });

    document.getElementById("pipeline").addEventListener("click", async (event) => {
      const button = event.target.closest(".harness-action");
      if (!button) return;
      const taskId = button.dataset.task;
      const action = button.dataset.action;
      const stage = button.dataset.stage || "plan_confirm";
      try {
        let result;
        if (action === "approve") {
          result = await api(`/api/sessions/${state.session_id}/tasks/${encodeURIComponent(taskId)}/approve`, {
            method: "POST",
            body: JSON.stringify({ stage, note: "approved in Studio" })
          });
        } else if (action === "reject") {
          const reason = prompt("Change request", "Please revise this stage.");
          if (!reason) return;
          result = await api(`/api/sessions/${state.session_id}/tasks/${encodeURIComponent(taskId)}/reject`, {
            method: "POST",
            body: JSON.stringify({ stage, reason })
          });
        } else {
          result = await api(`/api/sessions/${state.session_id}/tasks/${encodeURIComponent(taskId)}/continue`, {
            method: "POST",
            body: JSON.stringify({})
          });
        }
        state = result.state;
        if (result.action && result.action.message) {
          chatHistory.push(`PPT Agent: ${result.action.message}`);
        }
        render();
      } catch (error) {
        chatHistory.push(`PPT Agent: Harness action failed: ${error.message}`);
        render();
      }
    });

    document.getElementById("sendBtn").addEventListener("click", async () => {
      if (isSending) return;  // 防止重复发送
      
      const input = document.getElementById("message");
      const message = input.value.trim();
      if (!message) return;
      
      isSending = true;
      input.value = "";
      chatHistory.push(`You: ${message}`);
      renderEvents();
      
      try {
        const result = await api(`/api/sessions/${state.session_id}/chat`, { method: "POST", body: JSON.stringify({ message }) });
        state = result.state;
        
        // Add all new messages from the response (including progress events)
        const newMessages = result.messages || [];
        for (const msg of newMessages) {
          if (msg.startsWith('[Progress]')) {
            chatHistory.push(msg);
          } else if (!msg.startsWith('You:') && !msg.startsWith('PPT Agent:')) {
            // Only add agent messages, not user messages (already added)
            chatHistory.push(`PPT Agent: ${msg}`);
          } else if (msg.startsWith('PPT Agent:')) {
            chatHistory.push(msg);
          }
        }
        render();
        
        // Start polling if background task is running
        if (newMessages.some(m => m.includes('(Running in background...)'))) {
          lastEventIdx = (state.events || []).length;
          pollStatus();
        }
      } catch (error) {
        input.value = message;  // Restore input on error so user can retry
        chatHistory.push(`PPT Agent: Error: ${error.message}`);
      } finally {
        isSending = false;
        if (chatHistory.length > MAX_HISTORY) chatHistory = chatHistory.slice(-MAX_HISTORY);
        render();
      }
    });

    // Poll status for background tasks
    let statusPollInterval = null;
    let pollErrorCount = 0;
    let statusPollStartedAt = 0;
    let lastBusyNoticeAt = 0;
    async function pollStatus() {
      if (statusPollInterval) clearInterval(statusPollInterval);
      pollErrorCount = 0;
      statusPollStartedAt = Date.now();
      lastBusyNoticeAt = 0;
      statusPollInterval = setInterval(async () => {
        try {
          const status = await api(`/api/sessions/${state.session_id}/status`);
          pollErrorCount = 0;
          state = status.state;
          // Add new events using index-based dedup
          const offset = status.event_offset || 0;
          const events = status.events || [];
          for (let i = 0; i < events.length; i++) {
            const globalIdx = offset + i;
            if (globalIdx >= lastEventIdx) {
              const evt = events[i];
              if (!evt.startsWith('You:')) {
                chatHistory.push(evt.startsWith('[Progress]') || evt.startsWith('PPT Agent:') ? evt : `PPT Agent: ${evt}`);
              }
            }
          }
          lastEventIdx = Math.max(lastEventIdx, offset + events.length);
          renderEvents();
          if (status.busy) {
            const now = Date.now();
            if (!lastBusyNoticeAt || now - lastBusyNoticeAt >= 30000) {
              const elapsed = Math.max(1, Math.round((now - statusPollStartedAt) / 1000));
              chatHistory.push(`PPT Agent: Background task is still running (${elapsed}s elapsed).`);
              lastBusyNoticeAt = now;
              render();
            }
          }
          // Stop polling if task is done
          if (!status.busy) {
            clearInterval(statusPollInterval);
            statusPollInterval = null;
            if (status.last_error) {
              chatHistory.push(`PPT Agent: Task failed: ${status.last_error}`);
            } else {
              chatHistory.push("PPT Agent: Task completed!");
            }
            // Refresh evidence/materials after task completes
            loadMaterials();
            render();
          }
        } catch (e) {
          pollErrorCount++;
          console.error("Status poll error:", e);
          if (pollErrorCount >= 5) {
            clearInterval(statusPollInterval);
            statusPollInterval = null;
            chatHistory.push("PPT Agent: Lost connection to server. Please refresh the page.");
            render();
          }
        }
      }, 3000); // Poll every 3 seconds
    }
    document.getElementById("message").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        document.getElementById("sendBtn").click();
      }
    });
    document.getElementById("planBtn").addEventListener("click", async () => {
      try {
        const draft = state.draft_request || {};
        const result = await api(`/api/sessions/${state.session_id}/plans`, { method: "POST", body: JSON.stringify(draft) });
        state = result.state;
        render();
      } catch (error) {
        chatHistory.push(`PPT Agent: Plan error: ${error.message}`);
        render();
      }
    });
    document.getElementById("buildBtn").addEventListener("click", async () => {
      try {
        const result = await api(`/api/sessions/${state.session_id}/builds/approve`, { method: "POST" });
        state = result.state;
        if (result.result && result.result.message) {
          chatHistory.push(`PPT Agent: ${result.result.message}`);
        }
        render();
        // Start polling for background build
        if (result.result && result.result.ok) {
          lastEventIdx = (state.events || []).length;
          pollStatus();
        }
      } catch (error) {
        chatHistory.push(`PPT Agent: Build error: ${error.message}`);
        render();
      }
    });
    document.getElementById("planTab").addEventListener("click", () => setPreview("plan"));
    document.getElementById("stateTab").addEventListener("click", () => setPreview("state"));
    document.getElementById("thinkingTab").addEventListener("click", () => setPreview("thinking"));
    start().catch(error => {
      document.getElementById("sessionMeta").textContent = "failed";
      document.getElementById("events").textContent = error.message;
    });
  </script>
</body>
</html>"""
