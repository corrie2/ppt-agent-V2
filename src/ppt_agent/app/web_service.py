from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppt_agent.agent.chat_agent import ChatAgent
from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import register_default_skills
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.runtime.agent_skills import assign_skills_to_agents
from ppt_agent.runtime.harness.events import read_events
from ppt_agent.runtime.harness.manifest import HarnessManifest, load_manifest, task_root
from ppt_agent.runtime.harness.runner import HarnessRunner
from ppt_agent.shell.app import (
    _execute_skill_call,
    _merge_draft_into_generate_plan_arguments,
    _prepare_generate_plan_arguments,
    _qa_generated_plan,
    _set_pending_build_from_plan_result,
    registry_safe_arguments,
    run_agent_loop,
)
from ppt_agent.shell.session import ShellSession
from ppt_agent.storage.plan_io import read_plan_document


@dataclass
class WebSession:
    id: str
    shell: ShellSession
    registry: SkillRegistry
    agent: ChatAgent = field(default_factory=ChatAgent)
    events: list[str] = field(default_factory=list)
    background_task: Any = None  # threading.Thread for async skill execution
    conversation_history: list[dict[str, str]] = field(default_factory=list)  # [{"role": "user"/"assistant", "content": "..."}]
    last_error: str | None = None  # set when background task fails
    _lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)

    MAX_HISTORY_ROUNDS = 10  # keep last 10 rounds (20 messages)

    def emit(self, line: str) -> None:
        with self._lock:
            self.events.append(line)
            self.events = self.events[-200:]

    def add_to_history(self, role: str, content: str) -> None:
        """Add a message to conversation history, truncating if needed."""
        with self._lock:
            self.conversation_history.append({"role": role, "content": content})
            max_msgs = self.MAX_HISTORY_ROUNDS * 2
            if len(self.conversation_history) > max_msgs:
                self.conversation_history = self.conversation_history[-max_msgs:]


class WebSessionStore:
    SESSION_TTL = 7200  # 2 hours in seconds

    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}
        self._store_lock = threading.Lock()

    def _evict_expired(self) -> None:
        """Remove sessions that have exceeded the TTL."""
        with self._store_lock:
            now = time.time()
            expired = [
                sid
                for sid, s in self._sessions.items()
                if now - s.created_at > self.SESSION_TTL
                and (s.background_task is None or not s.background_task.is_alive())
            ]
            for sid in expired:
                del self._sessions[sid]

    def has(self, session_id: str) -> bool:
        """Check if a session exists and is not expired."""
        with self._store_lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if time.time() - session.created_at > self.SESSION_TTL:
                del self._sessions[session_id]
                return False
            return True

    def create(self, *, cwd: Path | None = None, assistant_enabled: bool = True) -> WebSession:
        self._evict_expired()
        shell = ShellSession.create(cwd)
        if assistant_enabled:
            shell.enable_assistant()
        registry = SkillRegistry()
        register_default_skills(registry, session=shell)
        web_session = WebSession(id=uuid4().hex, shell=shell, registry=registry)
        for warning in reload_user_skills(registry, session=shell):
            web_session.emit(f"Warning: {warning}")
        # Auto-scan workspace for existing evidence files
        self._restore_evidence_state(shell)
        with self._store_lock:
            self._sessions[web_session.id] = web_session
        return web_session

    def _restore_evidence_state(self, shell: ShellSession) -> None:
        """Scan workspace for existing evidence.json and restore state."""
        evidence_dir = shell.workspace_dir / ".ppt-agent" / "data" / "evidence"
        if not evidence_dir.exists():
            return
        # Find the most recently modified evidence.json
        latest_path = None
        latest_mtime = 0.0
        for evidence_file in evidence_dir.rglob("evidence.json"):
            try:
                mtime = evidence_file.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = evidence_file
            except OSError:
                continue
        if latest_path:
            shell.latest_evidence_path = str(latest_path)
            # Restore figure selection from disk
            selection_path = latest_path.parent / "selection.json"
            if selection_path.exists():
                try:
                    with open(selection_path, "r", encoding="utf-8") as f:
                        sel_data = json.load(f)
                    shell.selected_figure_ids = list(sel_data.get("selected_figure_ids", []))
                except (OSError, json.JSONDecodeError):
                    pass

    def get(self, session_id: str) -> WebSession:
        with self._store_lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"unknown session: {session_id}") from exc


class PptAgentWebService:
    def __init__(self) -> None:
        self.sessions = WebSessionStore()

    def create_session(self, *, cwd: str | None = None, assistant_enabled: bool = True) -> dict[str, Any]:
        if cwd is not None:
            resolved_cwd = Path(cwd).resolve()
            allowed_root = Path.home().resolve()
            if not resolved_cwd.is_relative_to(allowed_root):
                raise ValueError(f"cwd must be under {allowed_root}")
        web_session = self.sessions.create(
            cwd=Path(cwd).resolve() if cwd else None,
            assistant_enabled=assistant_enabled,
        )
        return self.state(web_session.id)

    def state(self, session_id: str) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        return {
            "session_id": web_session.id,
            "cwd": str(shell.cwd),
            "input_dir": str(shell.input_dir),
            "output_dir": str(shell.output_dir),
            "workspace_dir": str(shell.workspace_dir),  # Add workspace_dir
            "assistant_enabled": shell.assistant_enabled,
            "assistant_provider": shell.assistant_provider,
            "assistant_model": shell.assistant_model,
            "assistant_key_configured": shell.assistant_key_configured(),
            "files": shell.discovered_sources,
            "selected_sources": shell.selected_sources,
            "draft_request": shell.draft_request.to_generate_plan_arguments(shell.selected_pdf_paths()),
            "latest_plan_path": shell.latest_plan_path,
            "latest_evidence_path": shell.latest_evidence_path,
            "selected_figure_ids": list(shell.selected_figure_ids),
            "latest_ppt_path": shell.latest_ppt_path,
            "latest_html_path": shell.latest_html_path,
            "last_build_status": shell.last_build_status,
            "last_thinking": shell.last_thinking,
            "pending_action": _pending_action_payload(shell),
            "available_user_skills": shell.available_user_skills,
            "enabled_user_skills": shell.enabled_user_skills,
            "disabled_user_skills": shell.disabled_user_skills,
            "skill_catalog": _skill_catalog_payload(shell),
            "agent_skill_assignments": _agent_skill_assignments_payload(shell),
            "events": list(web_session.events),
            "last_error": web_session.last_error,
            "artifacts": self.artifacts(session_id),
            "harness_tasks": self.harness_tasks(session_id),
        }

    def scan_workspace(self, session_id: str, *, max_depth: int = 3) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        result = web_session.registry.invoke("scan_workspace", max_depth=max_depth)
        if result.get("reply"):
            web_session.emit(result["reply"])
        return {"result": result, "state": self.state(session_id)}

    ALLOWED_UPLOAD_SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".txt", ".markdown"}
    MAX_FILENAME_LENGTH = 200
    MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB

    def upload_files(self, session_id: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        """Upload files to the session's workspace directory.

        Args:
            files: list of (filename, file_bytes) tuples.
        """
        import logging
        import tempfile
        logger = logging.getLogger(__name__)

        web_session = self.sessions.get(session_id)
        workspace_dir = web_session.shell.workspace_dir
        workspace_dir.mkdir(parents=True, exist_ok=True)

        uploaded = []
        errors = []
        for filename, data in files:
            if len(data) > self.MAX_UPLOAD_BYTES:
                errors.append(f"{filename}: file too large ({len(data)} bytes, max {self.MAX_UPLOAD_BYTES})")
                continue
            from pathlib import Path
            suffix = Path(filename).suffix.lower()
            if suffix not in self.ALLOWED_UPLOAD_SUFFIXES:
                errors.append(f"{filename}: unsupported format ({suffix})")
                continue
            # Sanitize filename
            safe_name = Path(filename).name
            if len(safe_name) > self.MAX_FILENAME_LENGTH:
                safe_name = safe_name[:self.MAX_FILENAME_LENGTH - len(suffix)] + suffix
            dest = workspace_dir / safe_name
            # Avoid overwriting - add counter if exists
            if dest.exists():
                stem = dest.stem
                counter = 1
                while dest.exists():
                    dest = workspace_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
            # Atomic write: write to temp file then rename
            try:
                fd, tmp_path = tempfile.mkstemp(dir=str(workspace_dir), suffix=suffix)
                try:
                    with open(fd, "wb") as f:
                        f.write(data)
                    try:
                        Path(tmp_path).rename(dest)
                    except OSError:
                        # Cross-device link fallback
                        shutil.move(tmp_path, str(dest))
                finally:
                    # Clean up temp file if it still exists (rename/move succeeded)
                    if Path(tmp_path).exists():
                        try:
                            Path(tmp_path).unlink()
                        except OSError:
                            pass
            except OSError as exc:
                errors.append(f"{filename}: write failed ({exc})")
                continue
            uploaded.append({"name": dest.name, "path": str(dest), "size": len(data)})
            logger.info("Uploaded %s (%d bytes) to %s", dest.name, len(data), dest)

        # Auto-scan workspace after upload (background thread to avoid blocking)
        if uploaded:
            import threading
            def _bg_scan():
                try:
                    web_session.registry.invoke("scan_workspace", max_depth=3)
                except Exception as exc:
                    logger.warning("Post-upload scan failed: %s", exc)
            threading.Thread(target=_bg_scan, daemon=True).start()

        web_session.emit(f"Uploaded {len(uploaded)} file(s): {', '.join(f['name'] for f in uploaded)}")
        return {
            "uploaded": uploaded,
            "errors": errors,
            "state": self.state(session_id),
        }

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        registry = web_session.registry
        
        # Set the output_fn callback for progress logging
        shell.output_fn = web_session.emit
        
        shell.remember_message("user", message)
        web_session.add_to_history("user", message)
        events_snapshot = list(web_session.events)
        before = len(events_snapshot)

        # Check if a background task is running
        with web_session._lock:
            task_alive = web_session.background_task is not None and web_session.background_task.is_alive()
        if task_alive:
            web_session.emit("(Previous task still running in background...)")
            return {
                "messages": list(web_session.events)[before:],
                "loop_state": _safe_loop_state(shell.last_loop_state),
                "state": self.state(session_id),
            }

        loop_state = run_agent_loop(message, session=shell, registry=registry, output_fn=web_session.emit)
        if loop_state.terminal_reason == "not_handled":
            decision = web_session.agent.respond(shell, message, registry, history=web_session.conversation_history)
            web_session.emit(decision.reply)
            web_session.add_to_history("assistant", decision.reply)
            if decision.thinking:
                shell.last_thinking = decision.thinking
            
            # Check if any skill calls are long-running
            LONG_RUNNING_SKILLS = {"generate_plan", "build_ppt", "revise_plan", "run_from_plan"}
            has_long_running = any(sc.name in LONG_RUNNING_SKILLS for sc in decision.skill_calls)
            
            if has_long_running:
                # Run all skill calls in background thread
                import threading
                def _run_skills_in_background():
                    try:
                        for skill_call in decision.skill_calls:
                            try:
                                result = _execute_skill_call(skill_call, session=shell, registry=registry, output_fn=web_session.emit)
                                if not isinstance(result, dict) or result.get("ok") is False:
                                    with web_session._lock:
                                        web_session.last_error = (result.get("reply") if isinstance(result, dict) else None) or f"{skill_call.name} failed"
                            except Exception as exc:
                                import logging
                                logging.getLogger(__name__).error("Background skill %s failed: %s", skill_call.name, exc, exc_info=True)
                                web_session.emit(f"Error: {skill_call.name} failed - {exc}")
                                with web_session._lock:
                                    web_session.last_error = str(exc)
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error("Background task crashed: %s", exc, exc_info=True)
                        web_session.emit(f"Error: Background task crashed - {exc}")
                        with web_session._lock:
                            web_session.last_error = str(exc)
                    finally:
                        with web_session._lock:
                            web_session.background_task = None
                
                with web_session._lock:
                    web_session.last_error = None  # Clear previous error
                    web_session.background_task = threading.Thread(target=_run_skills_in_background, daemon=True)
                    web_session.background_task.start()
                web_session.emit("(Running in background... You can continue chatting while this completes.)")
            else:
                # Execute synchronously (short tasks)
                for skill_call in decision.skill_calls:
                    _execute_skill_call(skill_call, session=shell, registry=registry, output_fn=web_session.emit)

        return {
            "messages": list(web_session.events)[before:],
            "loop_state": _safe_loop_state(shell.last_loop_state),
            "state": self.state(session_id),
        }

    def generate_plan(self, session_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        registry = web_session.registry
        
        # Set the output_fn callback for progress logging
        shell.output_fn = web_session.emit
        
        merged = _merge_draft_into_generate_plan_arguments(shell, registry_safe_arguments(arguments))
        prepared = _prepare_generate_plan_arguments(shell, registry=registry, arguments=merged, output_fn=web_session.emit)
        web_session.emit("-> generating plan with evidence...")
        result = registry.invoke("generate_plan", **prepared)
        if result.get("reply"):
            web_session.emit(result["reply"])
        _qa_generated_plan(result, session=shell, registry=registry, output_fn=web_session.emit)
        _set_pending_build_from_plan_result(result, session=shell, output_fn=web_session.emit)
        return {"result": result, "state": self.state(session_id)}

    def approve_build(self, session_id: str) -> dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        
        # Set the output_fn callback for progress logging
        shell.output_fn = web_session.emit
        
        action = shell.pending_action
        if not action:
            return {"result": {"ok": False, "message": "No pending build action."}, "state": self.state(session_id)}
        
        # Check if a background task is already running
        with web_session._lock:
            task_alive = web_session.background_task is not None and web_session.background_task.is_alive()
        if task_alive:
            return {"result": {"ok": False, "message": "Another task is already running."}, "state": self.state(session_id)}
        
        # Don't clear pending_action yet — only clear on success
        web_session.emit(f"-> {action.description}...")
        
        def _run_build_in_background():
            try:
                result = web_session.registry.invoke(action.skill_name, **action.arguments)
                if result.get("reply"):
                    web_session.emit(result["reply"])
                if result.get("ok") is False:
                    with web_session._lock:
                        web_session.last_error = result.get("reply") or "Build failed"
                else:
                    # Only clear pending_action on success
                    with web_session._lock:
                        shell.pending_action = None
            except Exception as exc:
                logger.error("Background build failed: %s", exc, exc_info=True)
                web_session.emit(f"Error: Build failed - {exc}")
                with web_session._lock:
                    web_session.last_error = str(exc)
            finally:
                with web_session._lock:
                    web_session.background_task = None
        
        with web_session._lock:
            web_session.last_error = None
            web_session.background_task = threading.Thread(target=_run_build_in_background, daemon=True)
            web_session.background_task.start()
        
        web_session.emit("(Running build in background... You can continue chatting while this completes.)")
        return {"result": {"ok": True, "message": "Build started"}, "state": self.state(session_id)}

    def latest_plan(self, session_id: str) -> dict[str, Any] | None:
        shell = self.sessions.get(session_id).shell
        if not shell.latest_plan_path:
            return None
        document = read_plan_document(Path(shell.latest_plan_path))
        return {
            "path": str(document.path),
            "payload": document.payload,
            "spec": document.spec.model_dump(mode="json"),
        }

    def skills(self, session_id: str) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        return {
            "available": web_session.shell.available_user_skills,
            "enabled": web_session.shell.enabled_user_skills,
            "disabled": web_session.shell.disabled_user_skills,
            "records": web_session.shell.user_skill_records,
            "catalog": _skill_catalog_payload(web_session.shell),
            "agent_assignments": _agent_skill_assignments_payload(web_session.shell),
        }

    def set_skill_disabled(self, session_id: str, skill_name: str, *, disabled: bool) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        if skill_name not in shell.available_user_skills:
            return {"ok": False, "message": f"Unknown user skill: {skill_name}", "state": self.state(session_id)}
        if disabled and skill_name not in shell.disabled_user_skills:
            shell.disabled_user_skills.append(skill_name)
        if not disabled:
            shell.disabled_user_skills = [name for name in shell.disabled_user_skills if name != skill_name]
        shell.enabled_user_skills = [name for name in shell.available_user_skills if name not in shell.disabled_user_skills]
        return {"ok": True, "state": self.state(session_id)}

    def artifacts(self, session_id: str) -> list[dict[str, Any]]:
        shell = self.sessions.get(session_id).shell
        output_dir = shell.output_dir
        if not output_dir.exists():
            return []
        artifacts: list[dict[str, Any]] = []
        for path in sorted(output_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            st = path.stat()
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "suffix": path.suffix.lower(),
                    "size": st.st_size,
                    "modified_time": st.st_mtime,
                }
            )
        return artifacts[:50]

    def harness_tasks(self, session_id: str) -> list[dict[str, Any]]:
        """List archived multi-agent task runs for the current workspace."""
        shell = self.sessions.get(session_id).shell
        root = task_root(shell.cwd)
        if not root.exists():
            return []
        tasks: list[dict[str, Any]] = []
        for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            try:
                manifest = load_manifest(path)
            except Exception:
                continue
            payload = self._harness_manifest_summary(path, manifest)
            payload["modified_time"] = path.stat().st_mtime
            tasks.append(payload)
        return tasks[:100]

    def harness_task(self, session_id: str, task_id: str) -> dict[str, Any]:
        task_dir = self._harness_task_dir(session_id, task_id)
        manifest = load_manifest(task_dir)
        payload = self._harness_manifest_summary(task_dir, manifest)
        payload["manifest"] = manifest.model_dump(mode="json")
        payload["artifacts"] = self.harness_task_artifacts(session_id, task_id)
        payload["events"] = read_events(task_dir, limit=50)
        return payload

    def harness_task_events(self, session_id: str, task_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        task_dir = self._harness_task_dir(session_id, task_id)
        return read_events(task_dir, limit=limit)

    def harness_task_artifacts(self, session_id: str, task_id: str) -> list[dict[str, Any]]:
        task_dir = self._harness_task_dir(session_id, task_id)
        manifest = load_manifest(task_dir)
        artifacts: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(kind: str, path_value: str, *, stage: str | None = None) -> None:
            path = (task_dir / path_value).resolve()
            if str(path) in seen:
                return
            seen.add(str(path))
            item: dict[str, Any] = {
                "stage": stage,
                "kind": kind,
                "path": str(path),
                "relative_path": path_value,
                "exists": path.exists(),
            }
            if path.exists() and path.is_file():
                stat = path.stat()
                item.update({"name": path.name, "suffix": path.suffix.lower(), "size": stat.st_size})
            artifacts.append(item)

        for stage in manifest.stages:
            stage_paths = {
                "input": stage.input_path,
                "output": stage.output_path,
                "eval": stage.eval_path,
                "status": stage.status_path,
            }
            for kind, path_value in stage_paths.items():
                if path_value:
                    add(kind, path_value, stage=stage.id)
        for kind, path_value in manifest.outputs.items():
            add(f"output:{kind}", path_value)
        for kind, path_value in manifest.reports.items():
            add(f"report:{kind}", path_value)
        return artifacts

    def harness_task_continue(self, session_id: str, task_id: str, *, auto_rework: bool = False, max_rework: int = 1) -> dict[str, Any]:
        task_dir = self._harness_task_dir(session_id, task_id)
        action = HarnessRunner(task_dir).run_until_blocked(auto_rework=auto_rework, max_rework=max_rework)
        return {"action": action.model_dump(mode="json"), "state": self.state(session_id)}

    def harness_task_approve(self, session_id: str, task_id: str, *, stage: str, note: str = "approved") -> dict[str, Any]:
        task_dir = self._harness_task_dir(session_id, task_id)
        action = HarnessRunner(task_dir).approve(stage, note=note)
        return {"action": action.model_dump(mode="json"), "state": self.state(session_id)}

    def harness_task_reject(self, session_id: str, task_id: str, *, stage: str, reason: str) -> dict[str, Any]:
        task_dir = self._harness_task_dir(session_id, task_id)
        action = HarnessRunner(task_dir).reject(stage, reason=reason)
        return {"action": action.model_dump(mode="json"), "state": self.state(session_id)}

    def harness_task_gates(self, session_id: str, task_id: str, *, stage: str) -> dict[str, Any]:
        task_dir = self._harness_task_dir(session_id, task_id)
        gate = HarnessRunner(task_dir).run_stage_gate(stage)
        return {"gate": gate.model_dump(mode="json"), "state": self.state(session_id)}

    def _harness_task_dir(self, session_id: str, task_id: str) -> Path:
        shell = self.sessions.get(session_id).shell
        root = task_root(shell.cwd).resolve()
        task_dir = (root / Path(task_id).name).resolve()
        if not task_dir.is_relative_to(root) or not (task_dir / "manifest.json").exists():
            raise KeyError(f"unknown Harness task: {task_id}")
        return task_dir

    @staticmethod
    def _harness_manifest_summary(task_dir: Path, manifest: HarnessManifest) -> dict[str, Any]:
        completed = sum(1 for stage in manifest.stages if stage.status in {"passed", "completed", "approved", "skipped"})
        failed = sum(1 for stage in manifest.stages if stage.status in {"failed", "needs_rework"})
        waiting = sum(1 for stage in manifest.stages if stage.status == "waiting_approval")
        return {
            "task_id": task_dir.name,
            "task_dir": str(task_dir),
            "title": manifest.topic,
            "topic": manifest.topic,
            "status": manifest.status,
            "current_stage": manifest.current_stage,
            "created_at": manifest.created_at,
            "updated_at": manifest.updated_at,
            "total_stages": len(manifest.stages),
            "completed_stages": completed,
            "failed_stages": failed,
            "waiting_approval_stages": waiting,
            "outputs": dict(manifest.outputs),
            "reports": dict(manifest.reports),
        }

    def parse_pdf(self, session_id: str, pdf_path: str) -> dict[str, Any]:
        """Parse a PDF with MinerU to extract text and figures (runs in background)."""
        import logging
        logger = logging.getLogger(__name__)
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        shell.output_fn = web_session.emit
        
        # Check if a background task is already running
        with web_session._lock:
            task_alive = web_session.background_task is not None and web_session.background_task.is_alive()
        if task_alive:
            return {"ok": False, "message": "Another task is already running.", "state": self.state(session_id)}
        
        web_session.emit(f"-> Parsing PDF: {pdf_path}...")
        
        def _run_parse_in_background():
            try:
                result = web_session.registry.invoke("parse_pdf", pdf_path=pdf_path)
                if result.get("reply"):
                    web_session.emit(result["reply"])
                if result.get("ok") is False:
                    with web_session._lock:
                        web_session.last_error = result.get("reply") or "PDF parsing failed"
            except Exception as exc:
                logger.error("Background parse_pdf failed: %s", exc, exc_info=True)
                web_session.emit(f"Error: PDF parsing failed - {exc}")
                with web_session._lock:
                    web_session.last_error = str(exc)
            finally:
                with web_session._lock:
                    web_session.background_task = None
        
        with web_session._lock:
            web_session.last_error = None
            web_session.background_task = threading.Thread(target=_run_parse_in_background, daemon=True)
            web_session.background_task.start()
        
        web_session.emit("(Parsing PDF in background... This may take several minutes.)")
        return {"ok": True, "message": "PDF parsing started", "state": self.state(session_id)}

    def list_evidence_figures(self, session_id: str) -> dict[str, Any]:
        """List all figures from the evidence pack."""
        shell = self.sessions.get(session_id).shell
        evidence_json, evidence_dir = self._resolve_evidence_path(session_id)

        if not evidence_json or not evidence_json.exists():
            return {"figures": [], "selected_ids": list(shell.selected_figure_ids)}

        try:
            with open(evidence_json, "r", encoding="utf-8") as f:
                pack_data = json.load(f)
        except Exception:
            return {"figures": [], "selected_ids": list(shell.selected_figure_ids)}

        figures_dir = evidence_dir / "figures"
        figure_list = []
        for fig in pack_data.get("figures", []):
            fig_id = fig.get("id", "")
            fig_path = fig.get("path", "")
            # Resolve image path
            if fig_path and not Path(fig_path).is_absolute():
                fig_path = str(evidence_dir / fig_path)
            # Check if image file exists
            has_image = fig_path and Path(fig_path).exists()
            figure_list.append({
                "id": fig_id,
                "caption": fig.get("caption", "") or fig.get("text", ""),
                "page": fig.get("page"),
                "source_file": fig.get("source_file", ""),
                "has_image": has_image,
            })

        return {
            "figures": figure_list,
            "selected_ids": list(shell.selected_figure_ids),
        }

    def select_evidence_figures(self, session_id: str, figure_ids: list[str]) -> dict[str, Any]:
        """Store user's figure selection (persisted to disk)."""
        shell = self.sessions.get(session_id).shell
        shell.selected_figure_ids = list(figure_ids)
        # Persist to disk alongside evidence.json
        _persist_figure_selection(shell)
        return {"ok": True, "selected_ids": list(shell.selected_figure_ids)}

    def get_figure_image(self, session_id: str, figure_id: str) -> Path:
        """Get the image file path for a specific figure."""
        from fastapi import HTTPException
        shell = self.sessions.get(session_id).shell
        evidence_path = shell.latest_evidence_path
        workspace_dir = shell.workspace_dir.resolve()

        # Fallback: search workspace for evidence.json
        if not evidence_path:
            evidence_dir = shell.workspace_dir / ".ppt-agent" / "data" / "evidence"
            if evidence_dir.exists():
                candidates = sorted(evidence_dir.rglob("evidence.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    evidence_path = str(candidates[0])

        if not evidence_path:
            raise HTTPException(status_code=404, detail="No evidence pack found.")

        evidence_json = Path(evidence_path)
        if evidence_json.is_dir():
            evidence_dir = evidence_json
            evidence_json = evidence_dir / "evidence.json"
        else:
            evidence_dir = evidence_json.parent

        if not evidence_json.exists():
            raise HTTPException(status_code=404, detail="Evidence pack not found.")

        import json
        with open(evidence_json, "r", encoding="utf-8") as f:
            pack_data = json.load(f)

        for fig in pack_data.get("figures", []):
            if fig.get("id") == figure_id:
                fig_path = fig.get("path", "")
                if fig_path:
                    if Path(fig_path).is_absolute():
                        # Absolute path - validate it's within workspace (not just evidence_dir)
                        # Images are stored in .ppt-agent/ingest/ but evidence.json is in .ppt-agent/data/evidence/
                        resolved = Path(fig_path).resolve()
                        if resolved.exists() and resolved.is_file() and resolved.is_relative_to(workspace_dir):
                            return resolved
                    else:
                        # Relative path - resolve relative to evidence_dir
                        fig_path = str(evidence_dir / fig_path)
                        resolved = Path(fig_path).resolve()
                        if resolved.exists() and resolved.is_file() and self._safe_resolve(evidence_dir, fig.get("path", "")):
                            return resolved
                break

        raise HTTPException(status_code=404, detail=f"Figure {figure_id} image not found.")

    def _resolve_evidence_path(self, session_id: str) -> tuple[Path | None, Path | None]:
        """Resolve evidence.json path and its parent directory. Returns (evidence_json, evidence_dir)."""
        shell = self.sessions.get(session_id).shell
        evidence_path = shell.latest_evidence_path
        if not evidence_path:
            evidence_dir = shell.workspace_dir / ".ppt-agent" / "data" / "evidence"
            if evidence_dir.exists():
                candidates = sorted(evidence_dir.rglob("evidence.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    evidence_path = str(candidates[0])
        if not evidence_path:
            return None, None
        evidence_json = Path(evidence_path)
        if evidence_json.is_dir():
            return evidence_json / "evidence.json", evidence_json
        return evidence_json, evidence_json.parent

    @staticmethod
    def _safe_resolve(base_dir: Path, relative_path: str) -> Path | None:
        """Resolve a path safely, ensuring it's within base_dir. Returns None if invalid."""
        try:
            resolved = (base_dir / relative_path).resolve()
            if resolved.is_relative_to(base_dir.resolve()):
                return resolved
        except (ValueError, OSError):
            pass
        return None

    def list_evidence_sources(self, session_id: str) -> dict[str, Any]:
        """List all sources and their figures/tables grouped by PDF."""
        evidence_json, evidence_dir = self._resolve_evidence_path(session_id)
        if not evidence_json or not evidence_json.exists():
            return {"sources": []}
        with open(evidence_json, "r", encoding="utf-8") as f:
            pack_data = json.load(f)
        # Group figures by source_file
        sources: dict[str, dict] = {}
        for fig in pack_data.get("figures", []):
            src = fig.get("source_file", "unknown")
            if src not in sources:
                sources[src] = {"source_file": src, "figures": [], "tables": []}
            fig_path = fig.get("path", "")
            if fig_path and not Path(fig_path).is_absolute():
                fig_path = str(evidence_dir / fig_path)
            sources[src]["figures"].append({
                "id": fig.get("id"),
                "caption": fig.get("caption") or fig.get("text") or "",
                "page": fig.get("page"),
                "path": fig_path,
                "has_image": Path(fig_path).exists() if fig_path else False,
            })
        for tbl in pack_data.get("tables", []):
            src = tbl.get("source_file", "unknown")
            if src not in sources:
                sources[src] = {"source_file": src, "figures": [], "tables": []}
            tbl_path = tbl.get("path", "")
            if tbl_path and not Path(tbl_path).is_absolute():
                tbl_path = str(evidence_dir / tbl_path)
            sources[src]["tables"].append({
                "id": tbl.get("id"),
                "caption": tbl.get("caption") or tbl.get("text") or "",
                "page": tbl.get("page"),
                "path": tbl_path,
                "has_image": Path(tbl_path).exists() if tbl_path else False,
            })
        return {"sources": list(sources.values())}

    def delete_evidence_figure(self, session_id: str, figure_id: str) -> dict[str, Any]:
        """Delete a single figure from evidence."""
        evidence_json, evidence_dir = self._resolve_evidence_path(session_id)
        if not evidence_json or not evidence_json.exists():
            return {"ok": False, "error": "No evidence pack found"}
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        with web_session._lock:
            with open(evidence_json, "r", encoding="utf-8") as f:
                pack_data = json.load(f)
            figures = pack_data.get("figures", [])
            new_figures = []
            deleted = False
            for fig in figures:
                if fig.get("id") == figure_id:
                    raw_path = fig.get("path", "")
                    if raw_path:
                        safe = self._safe_resolve(evidence_dir, raw_path)
                        if safe and safe.exists() and safe.is_file():
                            safe.unlink()
                    deleted = True
                else:
                    new_figures.append(fig)
            if not deleted:
                return {"ok": False, "error": f"Figure {figure_id} not found"}
            pack_data["figures"] = new_figures
            with open(evidence_json, "w", encoding="utf-8") as f:
                json.dump(pack_data, f, ensure_ascii=False, indent=2)
            if figure_id in shell.selected_figure_ids:
                shell.selected_figure_ids.remove(figure_id)
        return {"ok": True, "deleted": figure_id}

    def delete_evidence_source(self, session_id: str, source_file: str) -> dict[str, Any]:
        """Delete all figures/tables from a specific source PDF."""
        evidence_json, evidence_dir = self._resolve_evidence_path(session_id)
        if not evidence_json or not evidence_json.exists():
            return {"ok": False, "error": "No evidence pack found"}
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        with web_session._lock:
            with open(evidence_json, "r", encoding="utf-8") as f:
                pack_data = json.load(f)
            deleted_ids = []
            new_figures = []
            for fig in pack_data.get("figures", []):
                if fig.get("source_file") == source_file:
                    raw_path = fig.get("path", "")
                    if raw_path:
                        safe = self._safe_resolve(evidence_dir, raw_path)
                        if safe and safe.exists() and safe.is_file():
                            safe.unlink()
                    deleted_ids.append(fig.get("id"))
                    if fig.get("id") in shell.selected_figure_ids:
                        shell.selected_figure_ids.remove(fig.get("id"))
                else:
                    new_figures.append(fig)
            new_tables = []
            for tbl in pack_data.get("tables", []):
                if tbl.get("source_file") == source_file:
                    raw_path = tbl.get("path", "")
                    if raw_path:
                        safe = self._safe_resolve(evidence_dir, raw_path)
                        if safe and safe.exists() and safe.is_file():
                            safe.unlink()
                    deleted_ids.append(tbl.get("id"))
                else:
                    new_tables.append(tbl)
            new_sections = [s for s in pack_data.get("sections", []) if s.get("source_file") != source_file]
            pack_data["figures"] = new_figures
            pack_data["tables"] = new_tables
            pack_data["sections"] = new_sections
            with open(evidence_json, "w", encoding="utf-8") as f:
                json.dump(pack_data, f, ensure_ascii=False, indent=2)
        return {"ok": True, "deleted_ids": deleted_ids, "source_file": source_file}


def _persist_figure_selection(shell: ShellSession) -> None:
    """Write selected_figure_ids to selection.json next to evidence.json."""
    evidence_path = shell.latest_evidence_path
    if not evidence_path:
        return
    from pathlib import Path
    evidence_json = Path(evidence_path)
    if evidence_json.is_dir():
        selection_path = evidence_json / "selection.json"
    else:
        selection_path = evidence_json.parent / "selection.json"
    try:
        with open(selection_path, "w", encoding="utf-8") as f:
            json.dump({"selected_figure_ids": list(shell.selected_figure_ids)}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _pending_action_payload(shell: ShellSession) -> dict[str, Any] | None:
    if not shell.pending_action:
        return None
    return {
        "skill_name": shell.pending_action.skill_name,
        "arguments": shell.pending_action.arguments,
        "description": shell.pending_action.description,
    }


def _skill_catalog_payload(shell: ShellSession) -> list[dict[str, Any]]:
    enabled = set(shell.enabled_user_skills)
    disabled = set(shell.disabled_user_skills)
    catalog: list[dict[str, Any]] = []
    for record in shell.user_skill_records:
        name = str(record.get("name") or "")
        if not name:
            continue
        catalog.append(
            {
                "name": name,
                "description": record.get("description") or "",
                "when_to_use": record.get("when_to_use"),
                "source": record.get("source") or "",
                "path": record.get("path") or "",
                "skill_md_path": record.get("skill_md_path"),
                "allowed_builtin_skills": record.get("allowed_builtin_skills") or [],
                "enabled": bool(record.get("enabled")) and name in enabled,
                "session_disabled": name in disabled,
                "validation_errors": record.get("validation_errors") or [],
            }
        )
    return catalog


def _agent_skill_assignments_payload(shell: ShellSession) -> dict[str, list[dict[str, Any]]]:
    active_names = set(shell.enabled_user_skills)
    assignments = assign_skills_to_agents(shell.cwd)
    payload: dict[str, list[dict[str, Any]]] = {}
    for agent, skills in assignments.assignments.items():
        payload[agent] = [
            {
                "name": skill.name,
                "description": skill.description,
                "when_to_use": skill.when_to_use,
                "source": skill.source,
                "path": skill.path,
                "skill_md_path": skill.skill_md_path,
                "agent_scope": skill.agent_scope,
            }
            for skill in skills
            if skill.name in active_names
        ]
    return payload


def _safe_loop_state(loop_state: Any) -> dict[str, Any]:
    """Return only safe fields from loop_state, avoiding __dict__ exposure."""
    if loop_state is None:
        return {}
    return {
        "terminal_reason": getattr(loop_state, "terminal_reason", None),
        "pending_action": getattr(loop_state, "pending_action", None),
        "draft_request": getattr(loop_state, "draft_request", None),
    }
