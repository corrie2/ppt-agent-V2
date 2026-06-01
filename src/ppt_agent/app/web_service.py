from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppt_agent.agent.chat_agent import ChatAgent
from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import register_default_skills
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.runtime.agent_skills import assign_skills_to_agents
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

    MAX_HISTORY_ROUNDS = 10  # keep last 10 rounds (20 messages)

    def emit(self, line: str) -> None:
        self.events.append(line)
        self.events = self.events[-200:]

    def add_to_history(self, role: str, content: str) -> None:
        """Add a message to conversation history, truncating if needed."""
        self.conversation_history.append({"role": role, "content": content})
        max_msgs = self.MAX_HISTORY_ROUNDS * 2
        if len(self.conversation_history) > max_msgs:
            self.conversation_history = self.conversation_history[-max_msgs:]


class WebSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}

    def create(self, *, cwd: Path | None = None, assistant_enabled: bool = True) -> WebSession:
        shell = ShellSession.create(cwd)
        if assistant_enabled:
            shell.enable_assistant()
        registry = SkillRegistry()
        register_default_skills(registry, session=shell)
        web_session = WebSession(id=uuid4().hex, shell=shell, registry=registry)
        for warning in reload_user_skills(registry, session=shell):
            web_session.emit(f"Warning: {warning}")
        self._sessions[web_session.id] = web_session
        return web_session

    def get(self, session_id: str) -> WebSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc


class PptAgentWebService:
    def __init__(self) -> None:
        self.sessions = WebSessionStore()

    def create_session(self, *, cwd: str | None = None, assistant_enabled: bool = True) -> dict[str, Any]:
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
            "artifacts": self.artifacts(session_id),
        }

    def scan_workspace(self, session_id: str, *, max_depth: int = 3) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        result = web_session.registry.invoke("scan_workspace", max_depth=max_depth)
        if result.get("reply"):
            web_session.emit(result["reply"])
        return {"result": result, "state": self.state(session_id)}

    ALLOWED_UPLOAD_SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".txt", ".markdown"}

    def upload_files(self, session_id: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        """Upload files to the session's input directory.

        Args:
            files: list of (filename, file_bytes) tuples.
        """
        import logging
        logger = logging.getLogger(__name__)

        web_session = self.sessions.get(session_id)
        input_dir = web_session.shell.input_dir
        input_dir.mkdir(parents=True, exist_ok=True)

        uploaded = []
        errors = []
        for filename, data in files:
            from pathlib import Path
            suffix = Path(filename).suffix.lower()
            if suffix not in self.ALLOWED_UPLOAD_SUFFIXES:
                errors.append(f"{filename}: unsupported format ({suffix})")
                continue
            # Sanitize filename
            safe_name = Path(filename).name
            dest = input_dir / safe_name
            # Avoid overwriting - add counter if exists
            if dest.exists():
                stem = dest.stem
                counter = 1
                while dest.exists():
                    dest = input_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
            dest.write_bytes(data)
            uploaded.append({"name": dest.name, "path": str(dest), "size": len(data)})
            logger.info("Uploaded %s (%d bytes) to %s", dest.name, len(data), dest)

        # Auto-scan workspace after upload
        if uploaded:
            try:
                web_session.registry.invoke("scan_workspace", max_depth=3)
            except Exception as exc:
                logger.warning("Post-upload scan failed: %s", exc)

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
        before = len(web_session.events)

        # Check if a background task is running
        if web_session.background_task and web_session.background_task.is_alive():
            web_session.emit("(Previous task still running in background...)")
            return {
                "messages": web_session.events[before:],
                "loop_state": shell.last_loop_state.__dict__ if shell.last_loop_state else {},
                "state": self.state(session_id),
            }

        loop_state = run_agent_loop(message, session=shell, registry=registry, output_fn=web_session.emit)
        if loop_state.terminal_reason == "not_handled":
            web_session.add_to_history("user", message)
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
                                _execute_skill_call(skill_call, session=shell, registry=registry, output_fn=web_session.emit)
                            except Exception as exc:
                                import logging
                                logging.getLogger(__name__).error("Background skill %s failed: %s", skill_call.name, exc, exc_info=True)
                                web_session.emit(f"Error: {skill_call.name} failed - {exc}")
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).error("Background task crashed: %s", exc, exc_info=True)
                        web_session.emit(f"Error: Background task crashed - {exc}")
                    finally:
                        web_session.background_task = None
                
                web_session.background_task = threading.Thread(target=_run_skills_in_background, daemon=True)
                web_session.background_task.start()
                web_session.emit("(Running in background... You can continue chatting while this completes.)")
            else:
                # Execute synchronously (short tasks)
                for skill_call in decision.skill_calls:
                    _execute_skill_call(skill_call, session=shell, registry=registry, output_fn=web_session.emit)

        return {
            "messages": web_session.events[before:],
            "loop_state": shell.last_loop_state.__dict__,
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
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        
        # Set the output_fn callback for progress logging
        shell.output_fn = web_session.emit
        
        action = shell.pending_action
        if not action:
            return {"result": {"ok": False, "message": "No pending build action."}, "state": self.state(session_id)}
        web_session.emit(f"-> {action.description}...")
        result = web_session.registry.invoke(action.skill_name, **action.arguments)
        if result.get("reply"):
            web_session.emit(result["reply"])
        return {"result": result, "state": self.state(session_id)}

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

    def parse_pdf(self, session_id: str, pdf_path: str) -> dict[str, Any]:
        """Parse a PDF with MinerU to extract text and figures."""
        web_session = self.sessions.get(session_id)
        result = web_session.registry.invoke("parse_pdf", pdf_path=pdf_path)
        if result.get("reply"):
            web_session.emit(result["reply"])
        return {"ok": result.get("ok", False), "reply": result.get("reply"), "state": self.state(session_id)}

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
        """Store user's figure selection."""
        shell = self.sessions.get(session_id).shell
        shell.selected_figure_ids = list(figure_ids)
        return {"ok": True, "selected_ids": list(shell.selected_figure_ids)}

    def get_figure_image(self, session_id: str, figure_id: str) -> Path:
        """Get the image file path for a specific figure."""
        from fastapi import HTTPException
        shell = self.sessions.get(session_id).shell
        evidence_path = shell.latest_evidence_path

        # Fallback: search workspace for evidence.json
        if not evidence_path:
            evidence_dir = shell.workspace_dir / ".ppt-agent" / "data" / "evidence"
            if evidence_dir.exists():
                candidates = sorted(evidence_dir.rglob("evidence.json"), reverse=True)
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
                    if not Path(fig_path).is_absolute():
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
                candidates = sorted(evidence_dir.rglob("evidence.json"), reverse=True)
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
        with open(evidence_json, "r", encoding="utf-8") as f:
            pack_data = json.load(f)
        figures = pack_data.get("figures", [])
        new_figures = []
        deleted = False
        for fig in figures:
            if fig.get("id") == figure_id:
                # Delete image file (with path safety check)
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
        # Remove from selection
        shell = self.sessions.get(session_id).shell
        if figure_id in shell.selected_figure_ids:
            shell.selected_figure_ids.remove(figure_id)
        return {"ok": True, "deleted": figure_id}

    def delete_evidence_source(self, session_id: str, source_file: str) -> dict[str, Any]:
        """Delete all figures/tables from a specific source PDF."""
        evidence_json, evidence_dir = self._resolve_evidence_path(session_id)
        if not evidence_json or not evidence_json.exists():
            return {"ok": False, "error": "No evidence pack found"}
        with open(evidence_json, "r", encoding="utf-8") as f:
            pack_data = json.load(f)
        shell = self.sessions.get(session_id).shell
        deleted_ids = []
        # Filter out figures from this source
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
        # Filter out tables from this source
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
        # Filter out sections from this source
        new_sections = [s for s in pack_data.get("sections", []) if s.get("source_file") != source_file]
        pack_data["figures"] = new_figures
        pack_data["tables"] = new_tables
        pack_data["sections"] = new_sections
        with open(evidence_json, "w", encoding="utf-8") as f:
            json.dump(pack_data, f, ensure_ascii=False, indent=2)
        return {"ok": True, "deleted_ids": deleted_ids, "source_file": source_file}


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
