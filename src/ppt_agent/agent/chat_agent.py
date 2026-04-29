from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.llm.providers import PROVIDER_SPECS
from ppt_agent.shell.session import PendingUserRequest, ShellSession
from ppt_agent.storage.llm_settings import load_api_key


class SkillCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RouterDecision(BaseModel):
    reply: str
    skill_calls: list[SkillCall] = Field(default_factory=list)


class ChatAgent:
    def respond(self, session: ShellSession, message: str, registry: SkillRegistry | None = None) -> RouterDecision:
        if not session.assistant_enabled:
            return RouterDecision(
                reply="Current mode is manual CLI. Use /help to see commands, or use /files, /select, /plan, and /build.",
                skill_calls=[],
            )

        provider = session.assistant_provider
        model = session.assistant_model
        if not provider or not model:
            return RouterDecision(reply="AI assistant mode is missing provider/model configuration.", skill_calls=[])

        api_key = load_api_key(provider, session.cwd)
        if not api_key:
            return RouterDecision(
                reply=(
                    f"AI assistant mode is enabled, but no API key is configured for {provider}. "
                    f"Run `ppt-agent llm set-key {provider} --api-key <your-key>` or use `/ai off`."
                ),
                skill_calls=[],
            )

        try:
            return self._route_with_llm(session, message, registry=registry, api_key=api_key)
        except Exception:
            return self._route_with_fallback(session, message, registry=registry)

    def _route_with_llm(
        self,
        session: ShellSession,
        message: str,
        *,
        registry: SkillRegistry | None,
        api_key: str,
    ) -> RouterDecision:
        provider = session.assistant_provider
        model = session.assistant_model
        if not provider or not model:
            return self._route_with_fallback(session, message, registry=registry)

        payload = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(registry, enabled_user_skills=session.enabled_user_skills),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "message": message,
                            "cwd": str(session.cwd),
                            "input_dir": str(session.input_dir),
                            "output_dir": str(session.output_dir),
                            "latest_plan_path": session.latest_plan_path,
                            "latest_ppt_path": session.latest_ppt_path,
                            "selected_sources": session.selected_pdf_paths(),
                            "draft_request": {
                                "requested_pdf_name": session.draft_request.requested_pdf_name,
                                "topic": session.draft_request.topic,
                                "audience": session.draft_request.audience,
                                "tone": session.draft_request.tone,
                                "min_slides": session.draft_request.min_slides,
                                "slide_count": session.draft_request.slide_count,
                                "selected_sources": session.draft_request.selected_sources,
                            },
                            "discovered_sources": session.discovered_sources[:8],
                            "pending_action": session.pending_action.description if session.pending_action else None,
                            "provider": provider,
                            "model": model,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        response = httpx.post(
            f"{PROVIDER_SPECS[provider].base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = self._extract_json(content)
        return RouterDecision.model_validate(data)

    def _route_with_fallback(
        self,
        session: ShellSession,
        message: str,
        *,
        registry: SkillRegistry | None,
    ) -> RouterDecision:
        lower = message.lower()
        skill_calls: list[SkillCall] = []
        reply = "I will organize the current context and prepare a structured skill decision."

        if self._looks_like_revision(lower) and session.latest_plan_path and self._has_skill(registry, "revise_plan"):
            skill_calls.append(SkillCall(name="revise_plan", arguments={"revision": message}))
            reply = "I will revise the current plan."
            return RouterDecision(reply=reply, skill_calls=skill_calls)

        if self._looks_like_build(lower) and session.latest_plan_path and self._has_skill(registry, "build_ppt"):
            skill_calls.append(SkillCall(name="build_ppt", arguments={"plan_path": session.latest_plan_path}))
            reply = "I can prepare the current plan for build approval."
            return RouterDecision(reply=reply, skill_calls=skill_calls)

        if self._looks_like_planning(lower):
            pdfs = [item for item in session.discovered_sources if item["file_type"] == "pdf"]
            selected = self._resolve_sources_for_message(session, message)

            if not pdfs and self._has_skill(registry, "scan_workspace"):
                session.pending_user_request = PendingUserRequest(
                    text=message,
                    requested_source_names=self._extract_requested_source_names(message),
                    topic=self._extract_topic(message),
                    slides=self._extract_slide_count(message),
                    min_slides=self._extract_min_slide_count(message),
                    audience=self._extract_audience(message),
                    tone=self._extract_tone(message),
                )
                skill_calls.append(SkillCall(name="scan_workspace", arguments={"max_depth": 3}))
                reply = "I will scan the input directory first and check which PDFs are available."
                return RouterDecision(reply=reply, skill_calls=skill_calls)

            if len(pdfs) > 1 and not selected:
                if self._has_skill(registry, "list_sources"):
                    skill_calls.append(SkillCall(name="list_sources", arguments={}))
                reply = "There are multiple PDFs in the input directory. Use `/select 1` or `/select 1,2` first."
                return RouterDecision(reply=reply, skill_calls=skill_calls)

            if self._has_skill(registry, "generate_plan"):
                skill_calls.append(
                    SkillCall(
                        name="generate_plan",
                        arguments={
                            "topic": self._extract_topic(message),
                            "slides": self._extract_slide_count(message),
                            "min_slides": self._extract_min_slide_count(message),
                            "audience": self._extract_audience(message),
                            "tone": self._extract_tone(message),
                            "sources": selected or [item["path"] for item in pdfs],
                        },
                    )
                )
                reply = "I will generate a plan based on the selected PDFs, then wait for `/approve` before build."
                return RouterDecision(reply=reply, skill_calls=skill_calls)

        if self._looks_like_file_listing(lower) and self._has_skill(registry, "list_sources"):
            if not session.discovered_sources and self._has_skill(registry, "scan_workspace"):
                skill_calls.append(SkillCall(name="scan_workspace", arguments={"max_depth": 3}))
            skill_calls.append(SkillCall(name="list_sources", arguments={}))
            reply = "I will list the files found in the input directory."
            return RouterDecision(reply=reply, skill_calls=skill_calls)

        if session.latest_plan_path and self._has_skill(registry, "show_current_plan"):
            skill_calls.append(SkillCall(name="show_current_plan", arguments={}))
            reply = "I will show the current plan summary first."

        return RouterDecision(reply=reply, skill_calls=skill_calls)

    def _extract_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("router response did not contain JSON")
        return json.loads(text[start : end + 1])

    def _available_skills(self, registry: SkillRegistry | None, *, enabled_user_skills: list[str] | None = None) -> list[dict[str, Any]]:
        if registry is None:
            return [{"name": name} for name in self._default_skill_names()]
        descriptions: list[dict[str, Any]] = []
        budget = 8000
        used = 0
        enabled = set(enabled_user_skills or [])
        filter_user_skills = enabled_user_skills is not None
        for skill in sorted(registry.describe(), key=lambda item: item["name"]):
            if filter_user_skills and skill.get("source") != "built-in" and skill["name"] not in enabled:
                continue
            summary = {
                "name": skill["name"],
                "description": self._truncate(skill.get("description") or "", 250),
                "when_to_use": self._truncate(skill.get("when_to_use") or "", 250),
                "input_schema": skill.get("input_schema", {}),
                "source": skill.get("source", "built-in"),
                "type": skill.get("type", "builtin"),
                "requires_approval": skill.get("requires_approval", False),
            }
            size = len(json.dumps(summary, ensure_ascii=False))
            if used + size > budget:
                continue
            descriptions.append(summary)
            used += size
        return descriptions

    def _truncate(self, value: str, limit: int) -> str:
        return value if len(value) <= limit else value[: limit - 3] + "..."

    def _system_prompt(self, registry: SkillRegistry | None, *, enabled_user_skills: list[str] | None = None) -> str:
        sections = {
            "identity": "You are an orchestration agent for a PPT shell.",
            "behavior": "Return JSON only with keys: reply, skill_calls. Each skill_call must have name and arguments.",
            "tool_policy": (
                f"Available skills: {json.dumps(self._available_skills(registry, enabled_user_skills=enabled_user_skills), ensure_ascii=False)}. "
                "Fail closed on unknown tools or invalid arguments."
            ),
            "state_context": "The shell provides cwd, input/output dirs, sources, draft_request, latest artifacts, pending_action, provider and model.",
            "planning_policy": (
                "If selected_sources is non-empty, treat those PDFs as active unless the user explicitly changes files. "
                "Before generating a plan, use retrieve_project_memory and retrieve_failure_patterns when available. "
                "When the user gives feedback such as visual dislikes, too much body text, or preferred style, record it with record_project_memory. "
                "For file-writing actions, propose skill calls only; the shell requires explicit approval before build."
            ),
        }
        return "\n\n".join(f"## {name}\n{content}" for name, content in sections.items())

    def _default_skill_names(self) -> list[str]:
        return [
            "scan_workspace",
            "list_sources",
            "generate_plan",
            "retrieve_project_memory",
            "record_project_memory",
            "record_execution_trace",
            "retrieve_failure_patterns",
            "validate_plan",
            "migrate_plan",
            "build_ppt",
            "run_from_plan",
            "show_current_plan",
            "revise_plan",
            "list_generated_files",
        ]

    def _has_skill(self, registry: SkillRegistry | None, name: str) -> bool:
        return registry is None or name in registry.names()

    def _looks_like_planning(self, lower: str) -> bool:
        return any(
            token in lower
            for token in (
                "\u65b9\u6848",
                "\u8ba1\u5212",
                "plan",
                "ppt",
                "pdf",
                "\u751f\u6210",
                "\u505a",
                "\u505a\u4e00\u4efd",
                "deck",
            )
        )

    def _looks_like_file_listing(self, lower: str) -> bool:
        return any(token in lower for token in ("\u6587\u4ef6", "\u8d44\u6599", "pdf", "sources", "source", "list"))

    def _looks_like_revision(self, lower: str) -> bool:
        return any(token in lower for token in ("\u4fee\u6539", "\u8c03\u6574", "\u4f18\u5316", "revise", "update"))

    def _looks_like_build(self, lower: str) -> bool:
        return any(
            token in lower
            for token in ("build", "\u751f\u6210ppt", "\u751f\u6210 ppt", "\u5bfc\u51fa", "\u8f93\u51fappt", "\u8f93\u51fa ppt")
        )

    def _extract_slide_count(self, message: str) -> int | None:
        for pattern in (
            r"(\d+)\s*\u9875",
            r"(\d+)\s*\u5f20",
            r"(\d+)\s*slides?",
        ):
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _extract_min_slide_count(self, message: str) -> int | None:
        for pattern in (
            r"\u6570\u91cf\u5728\s*(\d+)\s*\u4ee5\u4e0a",
            r"(\d+)\s*\u9875\s*\u4ee5\u4e0a",
            r"(\d+)\s*\u5f20\s*\u4ee5\u4e0a",
            r"(\d+)\s*\+",
            r"at least\s+(\d+)\s*slides?",
            r"(\d+)\s*slides?\s*or more",
        ):
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _extract_audience(self, message: str) -> str | None:
        for pattern in (
            r"\u53d7\u4f17\u662f([^,，。；;]+)",
            r"\u9762\u5411([^,，。；;]+)",
            r"audience\s+is\s+([^,.;]+)",
        ):
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_tone(self, message: str) -> str | None:
        for pattern in (
            r"\u98ce\u683c\u662f([^,，。；;]+)",
            r"\u8bed\u6c14\u662f([^,，。；;]+)",
            r"tone\s+is\s+([^,.;]+)",
            r"style\s+is\s+([^,.;]+)",
        ):
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_topic(self, message: str) -> str:
        for pattern in (
            r"\u4e3b\u9898\u662f([^,，。；;]+)",
            r"topic\s+is\s+([^,.;]+)",
        ):
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return message

    def _extract_requested_source_names(self, message: str) -> list[str]:
        names: list[str] = []
        for match in re.finditer(r"([A-Za-z0-9][A-Za-z0-9 _.-]*?\.pdf)", message, flags=re.IGNORECASE):
            self._append_unique_name(names, match.group(1))

        for pattern in (
            r"(?:\u505a|\u4f7f\u7528|\u7528|\u57fa\u4e8e)\s*([A-Za-z0-9][A-Za-z0-9 _.-]{0,80})",
            r"(?:make|use|based on)\s+([A-Za-z0-9][A-Za-z0-9 _.-]{0,80})",
        ):
            for match in re.finditer(pattern, message, flags=re.IGNORECASE):
                candidate = re.split(r"[,，。；;\s]|\d", match.group(1).strip(), maxsplit=1)[0].strip(" .")
                if candidate.lower() not in {"pdf", "ppt", "deck", "plan", "slides"}:
                    self._append_unique_name(names, candidate)
        return names

    def _append_unique_name(self, names: list[str], value: str) -> None:
        normalized = value.strip()
        if normalized and normalized.lower() not in {name.lower() for name in names}:
            names.append(normalized)

    def _resolve_sources_for_message(self, session: ShellSession, message: str) -> list[str]:
        selected = session.selected_pdf_paths()
        normalized = message.lower()
        mentioned_paths: list[str] = []
        for item in session.discovered_sources:
            if item["file_type"] != "pdf":
                continue
            name = item["name"]
            stem = name.rsplit(".", 1)[0]
            if name.lower() in normalized or stem.lower() in normalized:
                if item["path"] not in mentioned_paths:
                    mentioned_paths.append(item["path"])

        if mentioned_paths:
            return mentioned_paths
        if selected and any(token in normalized for token in ("this pdf", "this file", "\u8fd9\u4e2apdf", "\u8fd9\u4e2a\u6587\u4ef6", "\u8fd9\u4e2a")):
            return selected
        return selected
