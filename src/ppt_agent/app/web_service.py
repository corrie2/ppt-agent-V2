from __future__ import annotations

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

    def emit(self, line: str) -> None:
        self.events.append(line)
        self.events = self.events[-200:]


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
            "assistant_enabled": shell.assistant_enabled,
            "assistant_provider": shell.assistant_provider,
            "assistant_model": shell.assistant_model,
            "assistant_key_configured": shell.assistant_key_configured(),
            "files": shell.discovered_sources,
            "selected_sources": shell.selected_sources,
            "draft_request": shell.draft_request.to_generate_plan_arguments(shell.selected_pdf_paths()),
            "latest_plan_path": shell.latest_plan_path,
            "latest_evidence_path": shell.latest_evidence_path,
            "latest_ppt_path": shell.latest_ppt_path,
            "latest_html_path": shell.latest_html_path,
            "last_build_status": shell.last_build_status,
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

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        web_session = self.sessions.get(session_id)
        shell = web_session.shell
        registry = web_session.registry
        shell.remember_message("user", message)
        before = len(web_session.events)

        loop_state = run_agent_loop(message, session=shell, registry=registry, output_fn=web_session.emit)
        if loop_state.terminal_reason == "not_handled":
            decision = web_session.agent.respond(shell, message, registry)
            web_session.emit(decision.reply)
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
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "suffix": path.suffix.lower(),
                    "size": path.stat().st_size,
                    "modified_time": path.stat().st_mtime,
                }
            )
        return artifacts[:50]


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
