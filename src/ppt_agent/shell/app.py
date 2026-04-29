from __future__ import annotations

from pathlib import Path
from typing import Callable

from ppt_agent.agent.chat_agent import ChatAgent, SkillCall
from ppt_agent.agent.skill_registry import SkillRegistry
from ppt_agent.agent.skills import register_default_skills
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.nodes.qa import qa_node
from ppt_agent.shell.draft import (
    draft_has_enough_for_plan,
    ensure_default_topic,
    merge_text_into_draft,
    render_draft_feedback,
    try_resolve_draft_sources,
)
from ppt_agent.shell.commands import (
    handle_command,
    is_approval_utterance,
    is_assistant_identity_query,
    is_build_status_query,
    is_cancel_utterance,
    is_datetime_query,
    render_build_status_response,
    render_continue_response,
    render_current_datetime_response,
    render_assistant_identity_response,
)
from ppt_agent.shell.session import AgentLoopState, PendingAction, PendingUserRequest, ShellSession
from ppt_agent.storage.project_memory import looks_like_user_preference
from ppt_agent.storage.plan_io import read_plan_document


def main() -> None:
    run_shell()


def run_shell(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    session: ShellSession | None = None,
    agent: ChatAgent | None = None,
    registry: SkillRegistry | None = None,
) -> None:
    current_session = session or ShellSession.create()
    current_agent = agent or ChatAgent()
    current_registry = registry or SkillRegistry()
    if not registry:
        register_default_skills(current_registry, session=current_session)
        for warning in reload_user_skills(current_registry, session=current_session):
            output_fn(f"Warning: {warning}")

    output_fn("ppt-agent shell")
    output_fn("Type /help for commands.")
    output_fn(f"Input directory: {current_session.input_dir}")
    output_fn(f"Output directory: {current_session.output_dir}")
    _prompt_assistant_mode(current_session, input_fn=input_fn, output_fn=output_fn)
    _prompt_user_skills(current_session, input_fn=input_fn, output_fn=output_fn)

    while True:
        try:
            raw = input_fn("ppt> ")
        except EOFError:
            output_fn("Bye.")
            return

        text = raw.strip()
        if not text:
            continue

        current_session.remember_message("user", text)
        if looks_like_user_preference(text) and "record_project_memory" in current_registry.names():
            current_registry.invoke("record_project_memory", feedback=text, source="user_feedback")
        if text.startswith("/"):
            if not handle_command(text, session=current_session, registry=current_registry, output_fn=output_fn):
                return
            continue

        if is_datetime_query(text):
            for line in render_current_datetime_response(text):
                output_fn(line)
            continue

        if is_assistant_identity_query(text):
            for line in render_assistant_identity_response(current_session):
                output_fn(line)
            continue

        if is_build_status_query(text):
            for line in render_build_status_response(current_session):
                output_fn(line)
            continue

        if current_session.pending_action:
            if is_approval_utterance(text):
                handle_command("/approve", session=current_session, registry=current_registry, output_fn=output_fn)
                continue
            if is_cancel_utterance(text):
                handle_command("/cancel", session=current_session, registry=current_registry, output_fn=output_fn)
                continue
            output_fn("There is a pending build action. Please confirm with /approve or cancel with /cancel.")
            continue

        disabled_skill = _explicit_disabled_user_skill(text, current_session)
        if disabled_skill:
            output_fn(f"{disabled_skill} is installed but not enabled for this session.")
            output_fn(f"Run `/skills enable {disabled_skill}` or restart and choose it at startup.")
            continue

        if is_approval_utterance(text):
            if current_session.assistant_enabled and _advance_draft_to_plan_if_possible(
                current_session, registry=current_registry, output_fn=output_fn, allow_default_topic=True
            ):
                continue
            for line in render_continue_response(current_session):
                output_fn(line)
            continue

        if not current_session.assistant_enabled:
            output_fn("Current mode is manual CLI. Enter /help to see commands, or use /files, /select, /plan, and /build.")
            continue

        if _handle_draft_request(text, session=current_session, registry=current_registry, output_fn=output_fn):
            continue

        decision = current_agent.respond(current_session, text, current_registry)
        output_fn(decision.reply)
        for skill_call in decision.skill_calls:
            _execute_skill_call(skill_call, session=current_session, registry=current_registry, output_fn=output_fn)


def _prompt_assistant_mode(session: ShellSession, *, input_fn, output_fn) -> None:
    output_fn("Enable AI assistant mode?")
    output_fn("1. Enable: use an LLM to chat with you and route local skills for PPT generation.")
    output_fn("2. Disable: enter manual CLI mode and run the commands yourself.")

    while True:
        try:
            choice = input_fn("Choose [1/2]: ").strip()
        except EOFError:
            session.disable_assistant()
            output_fn("Manual CLI mode enabled.")
            return

        if choice == "1":
            session.enable_assistant()
            output_fn("AI assistant mode enabled.")
            output_fn(f"provider/model: {session.assistant_provider}/{session.assistant_model}")
            if not session.assistant_key_configured():
                output_fn(
                    f"No API key configured for {session.assistant_provider}. "
                    f"Run `ppt-agent llm set-key {session.assistant_provider} --api-key <your-key>`."
                )
                output_fn("You can continue and configure the key later, or use `/ai off` to switch to manual mode.")
            return
        if choice == "2":
            session.disable_assistant()
            output_fn("Manual CLI mode enabled.")
            return
        output_fn("Please choose 1 or 2.")


def _prompt_user_skills(session: ShellSession, *, input_fn, output_fn) -> None:
    if not session.available_user_skills:
        output_fn("No user skills found.")
        return
    output_fn("Available user skills:")
    records = [record for record in session.user_skill_records if record.get("enabled") and record["name"] in session.available_user_skills]
    for index, record in enumerate(records, start=1):
        output_fn(f"{index}. {record['name']} - {record.get('description', '')}")
    output_fn("Choose skills for this session:")
    output_fn("0. None")
    output_fn("all. Enable all")
    output_fn("Or enter numbers, e.g. 1 or 1,2")
    try:
        choice = input_fn("Choose skills [0/all/1,2]: ").strip()
    except EOFError:
        choice = "0"
    selected: list[str] = []
    if choice.lower() == "all":
        selected = list(session.available_user_skills)
    elif choice and choice != "0":
        by_index = {str(index): record["name"] for index, record in enumerate(records, start=1)}
        for part in [item.strip() for item in choice.split(",") if item.strip()]:
            if part in by_index:
                selected.append(by_index[part])
            elif part in session.available_user_skills:
                selected.append(part)
    session.enabled_user_skills = _unique_existing_user_skills(selected, session)
    output_fn(
        "Enabled user skills: "
        + (", ".join(session.enabled_user_skills) if session.enabled_user_skills else "none")
    )


def _unique_existing_user_skills(names: list[str], session: ShellSession) -> list[str]:
    result: list[str] = []
    for name in names:
        if name in session.available_user_skills and name not in result:
            result.append(name)
    return result


def _explicit_disabled_user_skill(text: str, session: ShellSession) -> str | None:
    normalized = text.lower()
    for name in session.available_user_skills:
        if name.lower() in normalized and name not in session.enabled_user_skills:
            return name
    return None


def _execute_skill_call(skill_call: SkillCall, *, session: ShellSession, registry: SkillRegistry, output_fn) -> None:
    if skill_call.name == "build_ppt":
        arguments = registry.validate_arguments(skill_call.name, skill_call.arguments or {"plan_path": session.latest_plan_path})
        session.pending_action = PendingAction(
            skill_name="build_ppt",
            arguments=arguments,
            description="build PPT from the current plan",
        )
        session.last_build_status = "pending_approval"
        output_fn("Build is pending approval. Run /approve to continue.")
        return

    if skill_call.name == "generate_plan":
        skill_call.arguments = dict(skill_call.arguments)
        skill_call.arguments = _merge_draft_into_generate_plan_arguments(session, skill_call.arguments)
        if not skill_call.arguments.get("sources"):
            skill_call.arguments["sources"] = session.selected_pdf_paths() or [
                item["path"] for item in session.discovered_sources if item["file_type"] == "pdf"
            ]
        skill_call.arguments = _prepare_generate_plan_arguments(
            session,
            registry=registry,
            arguments=skill_call.arguments,
            output_fn=output_fn,
        )
        skill_call.arguments = registry.validate_arguments(skill_call.name, skill_call.arguments)
        if skill_call.arguments.get("sources"):
            session.selected_sources = list(skill_call.arguments["sources"])

    result = registry.invoke(skill_call.name, **skill_call.arguments)
    session.last_loop_state.last_skill_result = result
    reply = result.get("reply")
    if reply:
        output_fn(reply)
    if result.get("skill_markdown"):
        output_fn(f"Skill instructions loaded for this turn: {result.get('skill_name', skill_call.name)}")
        _advance_draft_to_plan_if_possible(session, registry=registry, output_fn=output_fn, allow_default_topic=True)
        return

    if skill_call.name == "scan_workspace":
        if result.get("files"):
            session.discovered_sources = result["files"]
        pdfs = [item["name"] for item in result.get("files", []) if item["file_type"] == "pdf"]
        if pdfs:
            output_fn(f"PDF sources: {', '.join(pdfs)}")
        _continue_pending_user_request_after_scan(session=session, registry=registry, output_fn=output_fn)
    if skill_call.name == "generate_plan":
        _qa_generated_plan(result, session=session, registry=registry, output_fn=output_fn)
        _set_pending_build_from_plan_result(result, session=session, output_fn=output_fn)


def _handle_draft_request(text: str, *, session: ShellSession, registry: SkillRegistry, output_fn) -> bool:
    state = run_agent_loop(text, session=session, registry=registry, output_fn=output_fn)
    return state.terminal_reason != "not_handled"


def run_agent_loop(
    user_input: str,
    *,
    session: ShellSession,
    registry: SkillRegistry,
    output_fn,
    max_auto_steps: int = 5,
) -> AgentLoopState:
    state = AgentLoopState(
        messages=list(session.recent_messages),
        pending_user_request=user_input,
        transition="user_input",
    )
    session.last_loop_state = state

    text = user_input
    extracted = merge_text_into_draft(session, text)
    state.turn_count += 1
    if extracted:
        state.transition = "draft_updated"
    if _is_plan_start_utterance(text) and _advance_draft_to_plan_if_possible(
        session, registry=registry, output_fn=output_fn, allow_default_topic=True
    ):
        state.transition = "plan_generated"
        state.needs_approval = True
        state.terminal_reason = "approval_required"
        return state
    if not extracted and not session.draft_request.selected_sources:
        state.terminal_reason = "not_handled"
        return state

    if not session.discovered_sources and (
        session.draft_request.requested_pdf_name or session.draft_request.requested_pdf_index
    ):
        if "scan_workspace" not in registry.names():
            state.terminal_reason = "not_handled"
            return state
        output_fn("-> scanning input directory...")
        state.transition = "user_input"
        state.turn_count += 1
        if state.turn_count > max_auto_steps:
            state.transition = "max_turns"
            state.terminal_reason = "max_turns"
            return state
        _execute_skill_call(SkillCall(name="scan_workspace", arguments={"max_depth": 3}), session=session, registry=registry, output_fn=output_fn)
        state.last_skill_result = {"skill": "scan_workspace", "files": session.discovered_sources}
        state.transition = "scan_completed"
        resolved, error = try_resolve_draft_sources(session)
        if error:
            for line in error.splitlines():
                output_fn(line)
            state.transition = "needs_clarification"
            state.needs_user_input = True
            state.terminal_reason = "needs_clarification"
            return state
        if resolved:
            output_fn(f"Matched source: {', '.join(Path(path).name for path in session.draft_request.selected_sources)}.")
            if session.draft_request.audience:
                output_fn(f"Using audience: {session.draft_request.audience}.")
            if session.draft_request.min_slides:
                output_fn(f"Using min slides: {session.draft_request.min_slides}.")
            state.transition = "source_resolved"
        if resolved and _advance_draft_to_plan_if_possible(
            session, registry=registry, output_fn=output_fn, allow_default_topic=True
        ):
            state.transition = "approval_required"
            state.needs_approval = True
            state.terminal_reason = "approval_required"
            return state
        for line in render_draft_feedback(session):
            output_fn(line)
        state.transition = "needs_clarification"
        state.needs_user_input = True
        state.terminal_reason = "needs_clarification"
        return state

    resolved, error = try_resolve_draft_sources(session)
    if error:
        for line in error.splitlines():
            output_fn(line)
        state.transition = "needs_clarification"
        state.needs_user_input = True
        state.terminal_reason = "needs_clarification"
        return state
    if resolved and extracted.get("requested_pdf_index"):
        output_fn(f"Selected PDFs: {', '.join(Path(path).name for path in session.draft_request.selected_sources)}")
        state.transition = "source_resolved"

    if _advance_draft_to_plan_if_possible(session, registry=registry, output_fn=output_fn, allow_default_topic=True):
        state.transition = "approval_required"
        state.needs_approval = True
        state.terminal_reason = "approval_required"
        return state
    if extracted:
        for line in render_draft_feedback(session):
            output_fn(line)
        state.transition = "needs_clarification"
        state.needs_user_input = True
        state.terminal_reason = "needs_clarification"
        return state
    state.terminal_reason = "not_handled"
    return state


def _advance_draft_to_plan_if_possible(
    session: ShellSession,
    *,
    registry: SkillRegistry,
    output_fn,
    allow_default_topic: bool = False,
) -> bool:
    if allow_default_topic:
        ensure_default_topic(session)
    if not (draft_has_enough_for_plan(session) and "generate_plan" in registry.names()):
        return False
    arguments = _prepare_generate_plan_arguments(
        session,
        registry=registry,
        arguments=_draft_generate_plan_arguments(session),
        output_fn=output_fn,
    )
    output_fn("-> generating plan with evidence...")
    result = registry.invoke("generate_plan", **arguments)
    reply = result.get("reply")
    if reply:
        output_fn(reply)
    _qa_generated_plan(result, session=session, registry=registry, output_fn=output_fn)
    _set_pending_build_from_plan_result(result, session=session, output_fn=output_fn)
    return True


def _is_plan_start_utterance(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "\u5f00\u59cb",
        "\u7ee7\u7eed",
        "\u751f\u6210",
        "\u751f\u6210\u8ba1\u5212",
        "\u5f00\u59cb\u751f\u6210",
        "\u5c31\u8fd9\u6837",
        "\u53ef\u4ee5",
        "start",
        "continue",
        "generate",
        "generate plan",
        "go",
    }


def _continue_pending_user_request_after_scan(*, session: ShellSession, registry: SkillRegistry, output_fn) -> None:
    request = session.pending_user_request
    if not request:
        return
    if "generate_plan" not in registry.names():
        session.pending_user_request = None
        return
    pdfs = [item for item in session.discovered_sources if item["file_type"] == "pdf"]
    if not request.requested_source_names:
        session.pending_user_request = None
        return

    matched, ambiguous = _match_requested_pdfs(request, pdfs)
    if ambiguous:
        output_fn(f"Multiple PDFs match '{ambiguous}': {', '.join(item['name'] for item in matched)}")
        output_fn("Use /select to choose the exact PDF, then repeat the request.")
        session.pending_user_request = None
        return
    if not matched:
        available = ", ".join(item["name"] for item in pdfs) if pdfs else "none"
        output_fn(f"Could not find the requested PDF: {', '.join(request.requested_source_names)}")
        output_fn(f"Available PDFs: {available}")
        session.pending_user_request = None
        return

    sources = [item["path"] for item in matched]
    session.selected_sources = sources
    session.draft_request.selected_sources = list(sources)
    if request.requested_source_names:
        session.draft_request.requested_pdf_name = request.requested_source_names[0]
    session.draft_request.merge(
        {
            "topic": request.topic,
            "slides": request.slides,
            "min_slides": request.min_slides,
            "audience": request.audience,
            "tone": request.tone,
        }
    )
    topic = request.topic or request.text
    if topic == request.text and request.requested_source_names:
        topic = Path(request.requested_source_names[0]).stem
    arguments = {
        "topic": topic,
        "sources": sources,
        "slides": request.slides,
        "min_slides": request.min_slides,
        "audience": request.audience,
        "tone": request.tone,
    }
    arguments = registry.validate_arguments("generate_plan", arguments)
    arguments = _prepare_generate_plan_arguments(session, registry=registry, arguments=arguments, output_fn=output_fn)
    result = registry.invoke("generate_plan", **arguments)
    session.pending_user_request = None
    reply = result.get("reply")
    if reply:
        output_fn(reply)
    _qa_generated_plan(result, session=session, registry=registry, output_fn=output_fn)
    _set_pending_build_from_plan_result(result, session=session, output_fn=output_fn)


def _match_requested_pdfs(request: PendingUserRequest, pdfs: list[dict]) -> tuple[list[dict], str | None]:
    selected: list[dict] = []
    for requested_name in request.requested_source_names:
        matches = [item for item in pdfs if _matches_pdf_name(requested_name, item["name"])]
        if len(request.requested_source_names) == 1 and len(matches) > 1:
            return matches, requested_name
        for item in matches:
            if item["path"] not in {existing["path"] for existing in selected}:
                selected.append(item)
    return selected, None


def _matches_pdf_name(requested_name: str, actual_name: str) -> bool:
    requested = _normalize_pdf_name(requested_name)
    actual = _normalize_pdf_name(actual_name)
    actual_stem = _normalize_pdf_name(Path(actual_name).stem)
    return requested in {actual, actual_stem} or requested in actual or actual_stem in requested


def _normalize_pdf_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return "".join(char for char in normalized if char.isalnum())


def _set_pending_build_from_plan_result(result: dict, *, session: ShellSession, output_fn) -> None:
    sources = result.get("sources", [])
    output_fn("Plan ready:")
    if session.current_request:
        output_fn(f"- Topic: {session.current_request}")
    if sources:
        output_fn(f"- Source PDFs: {', '.join(Path(path).name for path in sources)}")
        output_fn(f"Plan sources: {', '.join(Path(path).name for path in sources)}")
    if session.draft_request.audience:
        output_fn(f"- Audience: {session.draft_request.audience}")
    if result.get("plan_summary"):
        output_fn(f"- Summary: {result['plan_summary']}")
    output_format = result.get("output_format") or session.draft_request.output_format or "pptx"
    applied_skills = result.get("applied_skills") or session.draft_request.applied_skills
    if applied_skills:
        output_fn(f"- Applied skill: {', '.join(applied_skills)}")
    output_fn(f"- Output format: {output_format}")
    selected_names = [Path(path).name for path in session.selected_pdf_paths()]
    if output_format == "html" and "guizang-ppt-skill" in applied_skills:
        deck_name = f"{Path(selected_names[0]).stem}.html" if len(selected_names) == 1 else "shell-deck.html"
        skill_name = "build_html_deck"
        arguments = {
            "plan_path": result["plan_path"],
            "skill_name": "guizang-ppt-skill",
            "output_path": str(session.output_dir / deck_name),
            "theme": session.draft_request.theme or "magazine",
        }
        description = "build HTML deck from the generated plan"
        final_line = "Plan ready. Run /approve to build the HTML deck."
    else:
        deck_name = f"{Path(selected_names[0]).stem}.pptx" if len(selected_names) == 1 else "shell-deck.pptx"
        skill_name = "build_ppt"
        arguments = {"plan_path": result["plan_path"], "output_path": str(session.output_dir / deck_name)}
        description = "build PPT from the generated plan"
        final_line = "Plan ready. Run /approve to build the PPT."
    session.pending_action = PendingAction(
        skill_name=skill_name,
        arguments=arguments,
        description=description,
    )
    session.last_build_status = "pending_approval"
    output_fn(final_line)


def _draft_generate_plan_arguments(session: ShellSession) -> dict:
    return registry_safe_arguments(session.draft_request.to_generate_plan_arguments(session.selected_pdf_paths()))


def _prepare_generate_plan_arguments(
    session: ShellSession,
    *,
    registry: SkillRegistry,
    arguments: dict,
    output_fn,
) -> dict:
    prepared = registry_safe_arguments(dict(arguments))
    topic = prepared.get("topic") or session.draft_request.topic or session.current_request or "PPT"

    if "retrieve_project_memory" in registry.names():
        output_fn("-> retrieving project memory...")
        memory = registry.invoke("retrieve_project_memory", query=topic, limit=20)
        prepared["project_preferences"] = memory.get("preferences", [])
    if "retrieve_failure_patterns" in registry.names():
        failures = registry.invoke("retrieve_failure_patterns", query=topic, limit=20)
        prepared["failure_patterns"] = failures.get("failure_patterns", [])

    sources = list(prepared.get("sources") or session.selected_pdf_paths())
    if sources:
        session.selected_sources = list(sources)
        session.draft_request.selected_sources = list(sources)
        if "ingest_sources" in registry.names():
            output_fn("-> ingesting source evidence...")
            ingest_result = registry.invoke("ingest_sources", sources=sources)
            for warning in ingest_result.get("warnings", []):
                output_fn(f"Warning: {warning}")
        if "digest_pdf_sources" in registry.names():
            output_fn("-> digesting source evidence...")
            digest_result = registry.invoke("digest_pdf_sources", sources=sources)
            prepared["source_digest"] = digest_result.get("source_digest")
        if "retrieve_source_context" in registry.names():
            output_fn("-> retrieving source context...")
            context_result = registry.invoke("retrieve_source_context", sources=sources, query=topic, limit=5)
            prepared["source_context"] = context_result.get("contexts", [])
            for warning in context_result.get("warnings", []):
                output_fn(f"Warning: {warning}")

    if session.enabled_user_skills and not prepared.get("applied_skills"):
        chosen = session.enabled_user_skills[0]
        output_fn(f"-> loading enabled skill: {chosen}")
        prepared["applied_skills"] = [chosen]
    return registry_safe_arguments(prepared)


def _qa_generated_plan(result: dict, *, session: ShellSession, registry: SkillRegistry, output_fn) -> None:
    plan_path = result.get("plan_path")
    if not plan_path:
        return
    output_fn("-> running QA...")
    try:
        document = read_plan_document(Path(plan_path))
    except ValueError as exc:
        output_fn(f"QA skipped: {exc}")
        return
    qa_result = qa_node({"spec": document.spec.model_dump(mode="json")})
    issues = qa_result.get("qa_issues", [])
    if not issues:
        output_fn("QA passed.")
        return
    output_fn(f"QA found {len(issues)} issue(s).")
    for issue in issues[:5]:
        output_fn(f"- {issue.get('severity', 'warning')}: {issue.get('code')} - {issue.get('message')}")
    if "record_execution_trace" in registry.names():
        registry.invoke(
            "record_execution_trace",
            event="plan_qa_completed",
            trace_type="qa_failure",
            payload={"plan_path": plan_path, "issues": issues},
        )


def _merge_draft_into_generate_plan_arguments(session: ShellSession, arguments: dict) -> dict:
    merged = session.draft_request.to_generate_plan_arguments(arguments.get("sources") or session.selected_pdf_paths())
    merged.update({key: value for key, value in arguments.items() if value not in (None, [], "")})
    for key in ("topic", "audience", "tone", "min_slides", "slides", "sources", "output_format", "applied_skills", "theme", "skill_root", "skill_md_path"):
        value = getattr(session.draft_request, "slide_count" if key == "slides" else key, None)
        if key == "sources":
            value = session.draft_request.selected_sources
        if value:
            merged[key] = value
    return registry_safe_arguments(merged)


def registry_safe_arguments(arguments: dict) -> dict:
    return {key: value for key, value in arguments.items() if value not in (None, [], "")}
