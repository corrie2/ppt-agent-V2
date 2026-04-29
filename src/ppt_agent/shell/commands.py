from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ppt_agent.llm.providers import is_legacy_model
from ppt_agent.agent.skill_loader import skill_search_paths
from ppt_agent.agent.user_skills import reload_user_skills
from ppt_agent.shell.session import PendingAction, ShellSession
from ppt_agent.storage.plan_io import read_plan_document


def is_datetime_query(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    english_markers = {
        "what date is it",
        "what time is it",
        "what day is it",
        "current date",
        "current time",
        "today's date",
    }
    if normalized in english_markers:
        return True
    chinese_markers = (
        "\u4eca\u5929\u662f\u51e0\u53f7",
        "\u73b0\u5728\u51e0\u70b9",
        "\u5f53\u524d\u65e5\u671f",
        "\u4eca\u5929\u661f\u671f\u51e0",
        "\u73b0\u5728\u51e0\u70b9\u4e86",
    )
    if any(marker in normalized for marker in chinese_markers):
        return True
    return ("date" in normalized or "time" in normalized) and "model" not in normalized


def render_current_datetime_response(text: str) -> list[str]:
    now = datetime.now()
    weekday_en = now.strftime("%A")
    weekday_cn = ["\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09", "\u5468\u56db", "\u5468\u4e94", "\u5468\u516d", "\u5468\u65e5"][
        now.weekday()
    ]
    if _contains_cjk(text):
        return [
            f"\u4eca\u5929\u662f {now:%Y-%m-%d}\uff0c{weekday_cn}\u3002",
            f"\u5f53\u524d\u65f6\u95f4\uff1a{now:%H:%M:%S}",
        ]
    return [
        f"Today is {now:%Y-%m-%d} ({weekday_en}).",
        f"Current time: {now:%H:%M:%S}",
    ]


def is_assistant_identity_query(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False

    direct_phrases = {
        "what model are you",
        "which model are you using",
        "which model do you use",
        "what model do you use",
        "provider is what",
        "model is what",
    }
    if normalized in direct_phrases:
        return True

    if "current ai config" in normalized:
        return True
    if "provider" in normalized and any(token in normalized for token in {"what", "current", "which"}):
        return True
    if "model" in normalized and any(token in normalized for token in {"what", "current", "which", "using"}):
        return True
    if "deepseek" in normalized and any(token in normalized for token in {"are you", "using", "is"}):
        return True

    chinese_markers = [
        "\u4f60\u662f\u4ec0\u4e48\u6a21\u578b",
        "\u4f60\u7528\u7684\u4ec0\u4e48\u6a21\u578b",
        "\u5f53\u524d\u6a21\u578b\u662f\u4ec0\u4e48",
        "\u4f60\u662fdeepseek\u5417",
        "provider \u662f\u4ec0\u4e48",
        "model \u662f\u4ec0\u4e48",
        "\u5f53\u524d ai \u914d\u7f6e\u662f\u4ec0\u4e48",
        "\u5f53\u524dai\u914d\u7f6e\u662f\u4ec0\u4e48",
    ]
    return any(marker in normalized for marker in chinese_markers)


def render_assistant_identity_response(session: ShellSession) -> list[str]:
    provider = session.assistant_provider or "none"
    model = session.assistant_model or "none"
    key_status = "yes" if session.assistant_key_configured() else "no"
    mode_line = (
        "\u5f53\u524d AI assistant mode \u5df2\u5f00\u542f\u3002"
        if session.assistant_enabled
        else "\u5f53\u524d AI assistant mode \u672a\u5f00\u542f\u3002"
    )
    return [
        mode_line,
        f"assistant enabled: {'true' if session.assistant_enabled else 'false'}",
        f"Provider: {provider}",
        f"Model: {model}",
        f"Key configured: {key_status}",
    ]


def is_build_status_query(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    english_markers = {
        "is it done",
        "is the ppt done",
        "is the ppt ready",
        "is the deck done",
        "is the deck ready",
    }
    if normalized in english_markers:
        return True
    chinese_markers = (
        "\u505a\u597d\u4e86\u5417",
        "\u751f\u6210\u597d\u4e86\u5417",
        "\u5b8c\u6210\u4e86\u5417",
        "ppt\u505a\u597d\u4e86\u5417",
    )
    return any(marker in normalized for marker in chinese_markers)


def render_build_status_response(session: ShellSession) -> list[str]:
    if session.latest_ppt_path:
        source_names = session.selected_pdf_names() or [Path(path).name for path in session.latest_plan_sources]
        lines = [
            "\u5df2\u7ecf\u5b8c\u6210\u3002",
            f"PPT \u6587\u4ef6\uff1a{session.latest_ppt_path}",
            f"Plan \u6587\u4ef6\uff1a{session.latest_plan_path or 'none'}",
            f"Source PDFs\uff1a{', '.join(source_names) if source_names else 'none'}",
        ]
        return lines
    if session.pending_action:
        return [
            "\u8fd8\u6ca1\u6709\u5b8c\u6210\u3002",
            "\u5f53\u524d\u6709\u5f85\u786e\u8ba4\u7684\u6784\u5efa\u64cd\u4f5c\u3002",
            "\u8fd0\u884c /approve \u7ee7\u7eed\uff0c\u6216 /cancel \u53d6\u6d88\u3002",
            f"Plan \u6587\u4ef6\uff1a{session.latest_plan_path or 'none'}",
        ]
    if session.latest_plan_path:
        return [
            "\u8fd8\u6ca1\u6709\u751f\u6210 PPT\u3002",
            f"Plan \u6587\u4ef6\uff1a{session.latest_plan_path}",
            "\u53ef\u4ee5\u8fd0\u884c /build \u521b\u5efa\u5f85\u786e\u8ba4\u7684\u6784\u5efa\u52a8\u4f5c\uff0c\u7136\u540e /approve\u3002",
        ]
    return [
        "\u8fd8\u6ca1\u6709\u5f00\u59cb\u751f\u6210\u3002",
        "\u5148\u7528 /files \u67e5\u770b PDF\uff0c/select \u9009\u62e9\u6587\u4ef6\uff0c\u7136\u540e\u8bf4\u660e\u4f60\u7684 PPT \u9700\u6c42\u3002",
    ]


def render_continue_response(session: ShellSession) -> list[str]:
    if session.latest_ppt_path:
        return [
            "\u5f53\u524d\u6ca1\u6709\u5f85\u786e\u8ba4\u64cd\u4f5c\u3002\u4e0a\u4e00\u4efd PPT \u5df2\u751f\u6210\uff1a",
            session.latest_ppt_path,
            "\u5982\u679c\u8981\u4fee\u6539\uff0c\u8bf7\u544a\u8bc9\u6211\u8981\u6539\u54ea\u4e00\u9875\uff1b\u5982\u679c\u8981\u91cd\u65b0\u751f\u6210\uff0c\u8bf7\u8bf4\u660e\u65b0\u7684\u8981\u6c42\u3002",
        ]
    if session.latest_plan_path:
        return [
            "\u5f53\u524d\u6ca1\u6709\u5f85\u786e\u8ba4\u64cd\u4f5c\u3002",
            f"\u6700\u8fd1 plan\uff1a{session.latest_plan_path}",
            "\u5982\u679c\u8981\u751f\u6210 PPT\uff0c\u5148\u8fd0\u884c /build\uff0c\u7136\u540e /approve\u3002",
        ]
    return [
        "\u5f53\u524d\u6ca1\u6709\u5f85\u786e\u8ba4\u64cd\u4f5c\uff0c\u4e5f\u8fd8\u6ca1\u6709\u751f\u6210\u7ed3\u679c\u3002",
        "\u53ef\u4ee5\u5148\u8fd0\u884c /files \u67e5\u770b PDF\uff0c/select \u9009\u62e9\u6587\u4ef6\uff0c\u7136\u540e\u544a\u8bc9\u6211\u5236\u4f5c\u8981\u6c42\u3002",
    ]


def _resolve_user_path(session: ShellSession, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = session.cwd / path
    return path.resolve()


def _pdf_sources(session: ShellSession) -> list[dict]:
    return [item for item in session.discovered_sources if item["file_type"] == "pdf"]


def _refresh_sources_if_needed(session: ShellSession, registry) -> None:
    if session.discovered_sources:
        return
    result = registry.invoke("scan_workspace")
    if result.get("files"):
        session.discovered_sources = result["files"]


def _parse_selection(argument: str, pdfs: list[dict]) -> list[str]:
    if not argument.strip():
        raise ValueError("Provide at least one index or file name.")

    selected: list[str] = []
    by_index = {str(index): item["path"] for index, item in enumerate(pdfs, start=1)}
    by_name = {item["name"]: item["path"] for item in pdfs}
    by_relative = {item.get("relative_path", item["path"]): item["path"] for item in pdfs}

    for token in [part.strip() for part in argument.split(",") if part.strip()]:
        path = by_index.get(token) or by_name.get(token) or by_relative.get(token)
        if not path:
            raise ValueError(f"Unknown PDF selection: {token}")
        if path not in selected:
            selected.append(path)
    return selected


def _format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{int(size)} B"


def _format_pages(item: dict) -> str:
    pages = item.get("page_count")
    return str(pages) if isinstance(pages, int) and pages > 0 else "unknown"


def _render_pdf_row(index: int, item: dict, selected_paths: set[str]) -> str:
    marker = "*" if item["path"] in selected_paths else " "
    name = item["name"][:40]
    size = _format_size(item["size"])
    modified = item.get("modified_time", "unknown")
    pages = _format_pages(item)
    return f"[{index:>2}]{marker} {name:<40} {size:>10}  {modified:<16}  pages:{pages}"


def _render_preview(session: ShellSession, output_fn) -> None:
    if not session.latest_plan_path:
        output_fn("no plan available")
        return

    try:
        document = read_plan_document(Path(session.latest_plan_path))
    except ValueError:
        output_fn("no plan available")
        return

    request = document.payload.get("request", {})
    topic = session.current_request or request.get("topic") or document.spec.title
    selected_names = session.selected_pdf_names()
    plan_sources = [Path(path).name for path in session.latest_plan_sources]

    output_fn(f"request/topic: {topic}")
    output_fn(f"selected pdfs: {', '.join(selected_names) if selected_names else 'none'}")
    output_fn(f"output dir: {session.output_dir}")
    output_fn(f"latest plan: {session.latest_plan_path}")
    output_fn(f"plan title: {document.spec.title}")
    output_fn(f"plan slides: {len(document.spec.slides)}")
    output_fn(f"plan source pdfs: {', '.join(plan_sources) if plan_sources else 'none'}")
    output_fn(f"output format: {document.payload.get('output_format') or document.spec.output_format}")
    applied = document.payload.get("applied_skills") or document.spec.applied_skills
    output_fn(f"applied skill: {', '.join(applied) if applied else 'none'}")
    output_fn("slide outline:")
    for index, slide in enumerate(document.spec.slides, start=1):
        visual_type = slide.visual_type or "none"
        output_fn(f"  {index}. {slide.title} [visual_type: {visual_type}]")


def _assistant_status_lines(session: ShellSession) -> list[str]:
    provider = session.assistant_provider or "none"
    model = session.assistant_model or "none"
    key_status = "yes" if session.assistant_key_configured() else "no"
    lines = [
        f"assistant enabled: {'true' if session.assistant_enabled else 'false'}",
        f"provider: {provider}",
        f"model: {model}",
        f"key configured: {key_status}",
    ]
    if session.assistant_provider and session.assistant_model and is_legacy_model(session.assistant_provider, session.assistant_model):
        lines.append("model status: legacy compatibility")
    return lines


def is_approval_utterance(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "\u7ee7\u7eed",
        "\u662f",
        "\u786e\u8ba4",
        "\u53ef\u4ee5",
        "\u5f00\u59cb",
        "\u5f00\u59cb\u751f\u6210",
        "\u751f\u6210",
        "\u6267\u884c",
        "yes",
        "y",
        "go",
        "continue",
        "approve",
        "缁х画",
        "鏄?",
        "纭",
        "鍙互",
        "寮€濮?",
        "寮€濮嬬敓鎴?",
        "鐢熸垚",
        "鎵ц",
    }


def is_cancel_utterance(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "\u53d6\u6d88",
        "\u4e0d\u8981",
        "\u505c\u6b62",
        "\u7b97\u4e86",
        "cancel",
        "stop",
        "no",
        "n",
        "鍙栨秷",
        "涓嶈",
        "鍋滄",
        "绠椾簡",
    }


def _handle_ai_command(argument: str, *, session: ShellSession, output_fn) -> bool:
    action = argument.strip().lower()
    if action == "on":
        session.enable_assistant()
        output_fn("AI assistant mode enabled.")
        output_fn(f"provider/model: {session.assistant_provider}/{session.assistant_model}")
        if not session.assistant_key_configured():
            output_fn(
                f"No API key configured for {session.assistant_provider}. "
                f"Run `ppt-agent llm set-key {session.assistant_provider} --api-key <your-key>`."
            )
        return True
    if action == "off":
        session.disable_assistant()
        output_fn("Manual CLI mode enabled.")
        return True
    if action == "status":
        for line in _assistant_status_lines(session):
            output_fn(line)
        return True
    output_fn("Usage: /ai on | /ai off | /ai status")
    return True


def handle_command(raw: str, *, session: ShellSession, registry, output_fn) -> bool:
    command, *rest = raw.strip().split(maxsplit=1)
    argument = rest[0] if rest else ""

    if command == "/help":
        output_fn("/help /status /preview /input /output /files /select /plan /build /approve /cancel /skills list|inspect|reload|paths /ai on|off|status /exit")
        return True
    if command == "/skills":
        return _handle_skills_command(argument, session=session, registry=registry, output_fn=output_fn)
    if command == "/status":
        output_fn(f"mode: {session.mode_label()}")
        output_fn(f"cwd: {session.cwd}")
        output_fn(f"input dir: {session.input_dir}")
        output_fn(f"output dir: {session.output_dir}")
        output_fn(f"source files: {len(session.discovered_sources)}")
        selected_names = session.selected_pdf_names()
        if selected_names:
            output_fn(f"selected pdfs: {len(selected_names)}")
            output_fn(f"selected pdf file names: {', '.join(selected_names)}")
        else:
            output_fn("selected pdfs: none")
        if session.assistant_enabled:
            output_fn(f"provider: {session.assistant_provider or 'none'}")
            output_fn(f"model: {session.assistant_model or 'none'}")
            output_fn(f"key configured: {'yes' if session.assistant_key_configured() else 'no'}")
        output_fn(f"latest plan: {session.latest_plan_path or 'none'}")
        if session.latest_plan_sources:
            output_fn(f"latest plan sources: {', '.join(Path(path).name for path in session.latest_plan_sources)}")
        output_fn(f"latest ppt: {session.latest_ppt_path or 'none'}")
        output_fn(f"latest html: {session.latest_html_path or 'none'}")
        output_fn(f"last build status: {session.last_build_status or 'none'}")
        output_fn(f"pending action: {session.pending_action.description if session.pending_action else 'none'}")
        output_fn(
            "enabled user skills: "
            + (", ".join(session.enabled_user_skills) if session.enabled_user_skills else "none")
        )
        draft = session.draft_request
        output_fn(f"draft requested pdf: {draft.requested_pdf_name or 'none'}")
        output_fn(f"draft topic: {draft.topic or 'none'}")
        output_fn(f"draft audience: {draft.audience or 'none'}")
        output_fn(f"draft min slides: {draft.min_slides or 'none'}")
        output_fn(f"draft slide count: {draft.slide_count or 'none'}")
        output_fn(f"draft output format: {draft.output_format or 'none'}")
        output_fn(f"draft applied skills: {', '.join(draft.applied_skills) if draft.applied_skills else 'none'}")
        output_fn(
            "draft selected pdfs: "
            + (", ".join(Path(path).name for path in draft.selected_sources) if draft.selected_sources else "none")
        )
        return True
    if command == "/preview":
        _render_preview(session, output_fn)
        return True
    if command == "/input":
        if not argument:
            output_fn(f"input dir: {session.input_dir}")
            return True
        path = _resolve_user_path(session, argument)
        session.set_input_dir(path)
        output_fn(f"Input directory set to: {session.input_dir}")
        return True
    if command == "/output":
        if not argument:
            output_fn(f"output dir: {session.output_dir}")
            return True
        path = _resolve_user_path(session, argument)
        session.set_output_dir(path)
        output_fn(f"Output directory set to: {session.output_dir}")
        return True
    if command == "/files":
        _refresh_sources_if_needed(session, registry)
        files = registry.invoke("list_sources")
        pdfs = [item for item in files.get("files", []) if item["file_type"] == "pdf"]
        selected_paths = set(session.selected_pdf_paths())
        if pdfs:
            output_fn(" #  PDF file                                 Size  Modified time      Pages")
        for index, item in enumerate(pdfs, start=1):
            output_fn(_render_pdf_row(index, item, selected_paths))
        for item in files.get("files", []):
            if item["file_type"] != "pdf":
                output_fn(f"    {item['file_type']}: {item.get('relative_path', item['path'])}")
        if not files.get("files"):
            output_fn("No discovered source files.")
        return True
    if command == "/select":
        _refresh_sources_if_needed(session, registry)
        pdfs = _pdf_sources(session)
        if not pdfs:
            output_fn("No PDFs found in the input directory.")
            return True
        try:
            session.selected_sources = _parse_selection(argument, pdfs)
        except ValueError as exc:
            output_fn(str(exc))
            return True
        selected = ", ".join(Path(path).name for path in session.selected_sources)
        session.draft_request.selected_sources = list(session.selected_sources)
        if len(session.selected_sources) == 1:
            session.draft_request.requested_pdf_name = Path(session.selected_sources[0]).name
        output_fn(f"Selected PDFs: {selected}")
        if "ingest_sources" in registry.names():
            result = registry.invoke("ingest_sources", sources=session.selected_sources)
            for warning in result.get("warnings", []):
                output_fn(f"Warning: {warning}")
        return True
    if command == "/plan":
        if session.latest_plan_path:
            result = registry.invoke("show_current_plan")
            output_fn(result["reply"])
        elif session.current_request:
            result = registry.invoke("generate_plan", topic=session.current_request, sources=session.selected_pdf_paths())
            output_fn(result["reply"])
        else:
            output_fn("No current request or plan.")
        return True
    if command == "/build":
        if not session.latest_plan_path:
            output_fn("No latest plan available.")
            return True
        plan = read_plan_document(Path(session.latest_plan_path))
        if plan.payload.get("output_format") == "html" and "guizang-ppt-skill" in (plan.payload.get("applied_skills") or []):
            session.pending_action = PendingAction(
                skill_name="build_html_deck",
                arguments={
                    "plan_path": session.latest_plan_path,
                    "skill_name": "guizang-ppt-skill",
                    "output_path": str(session.output_dir / "shell-deck.html"),
                    "theme": plan.payload.get("theme") or "magazine",
                },
                description="build current HTML deck from latest plan",
            )
        else:
            session.pending_action = PendingAction(
                skill_name="build_ppt",
                arguments={"plan_path": session.latest_plan_path, "output_path": str(session.output_dir / "shell-deck.pptx")},
                description="build current PPT from latest plan",
            )
        session.last_build_status = "pending_approval"
        output_fn("Pending action created. Run /approve to build or /cancel to abort.")
        return True
    if command == "/approve":
        if not session.pending_action:
            output_fn("No pending action.")
            return True
        pending = session.pending_action
        session.pending_action = None
        result = registry.invoke(pending.skill_name, **pending.arguments)
        if pending.skill_name == "build_ppt" and result.get("ppt_path"):
            session.latest_ppt_path = result["ppt_path"]
            session.last_build_status = "completed"
            session.pending_action = None
        if pending.skill_name == "build_html_deck" and result.get("html_path"):
            session.latest_html_path = result["html_path"]
            session.latest_ppt_path = result["html_path"]
            session.last_build_status = "completed"
            session.pending_action = None
        output_fn(result["reply"])
        return True
    if command == "/cancel":
        session.pending_action = None
        if session.last_build_status == "pending_approval":
            session.last_build_status = "cancelled"
        output_fn("Pending action cleared.")
        return True
    if command == "/ai":
        return _handle_ai_command(argument, session=session, output_fn=output_fn)
    if command == "/exit":
        output_fn("Bye.")
        return False

    if argument:
        output_fn(f"Unknown command: {command} {argument}")
    else:
        output_fn(f"Unknown command: {command}")
    return True


def _handle_skills_command(argument: str, *, session: ShellSession, registry, output_fn) -> bool:
    action, *rest = argument.strip().split(maxsplit=1)
    detail = rest[0] if rest else ""
    if action == "paths":
        for source, path in skill_search_paths(session.cwd):
            output_fn(f"{source}: {path}")
        return True
    if action == "reload":
        warnings = reload_user_skills(registry, session=session)
        output_fn("Skills reloaded.")
        for warning in warnings:
            output_fn(f"Warning: {warning}")
        return True
    if action == "selected":
        output_fn(
            "enabled user skills: "
            + (", ".join(session.enabled_user_skills) if session.enabled_user_skills else "none")
        )
        return True
    if action in {"enable", "disable"} and detail:
        if detail not in session.available_user_skills:
            output_fn(f"Unknown user skill: {detail}")
            return True
        if action == "enable":
            if detail not in session.enabled_user_skills:
                session.enabled_user_skills.append(detail)
            output_fn(f"Enabled user skills: {', '.join(session.enabled_user_skills)}")
        else:
            session.enabled_user_skills = [name for name in session.enabled_user_skills if name != detail]
            output_fn(
                "enabled user skills: "
                + (", ".join(session.enabled_user_skills) if session.enabled_user_skills else "none")
            )
        return True
    if action == "list":
        records = _skill_records(session, registry)
        for item in records:
            status = "enabled" if item.get("enabled", True) else "invalid"
            invalid_reason = "; ".join(item.get("validation_errors") or [])
            output_fn(
                f"{item['name']} [{item.get('type', 'builtin')}] source:{item.get('source', 'built-in')} "
                f"claude-compatible:{'yes' if item.get('claude_compatible') else 'no'} {status} "
                f"{('- ' + invalid_reason) if invalid_reason else '- ' + item.get('description', '')}"
            )
        return True
    if action == "inspect" and detail:
        record = next((item for item in _skill_records(session, registry) if item["name"] == detail), None)
        if not record:
            output_fn(f"Unknown skill: {detail}")
            return True
        output_fn(f"name: {record['name']}")
        output_fn(f"type: {record.get('type', 'builtin')}")
        output_fn(f"source: {record.get('source', 'built-in')}")
        output_fn(f"claude-compatible: {'yes' if record.get('claude_compatible') else 'no'}")
        output_fn(f"enabled: {record.get('enabled', True)}")
        output_fn(f"path: {record.get('path') or 'none'}")
        output_fn(f"skill root: {record.get('skill_root') or record.get('path') or 'none'}")
        output_fn(f"SKILL.md: {record.get('skill_md_path') or 'none'}")
        output_fn(f"assets: {'exists' if record.get('assets_dir') else 'missing'}")
        output_fn(f"references: {'exists' if record.get('references_dir') else 'missing'}")
        output_fn(f"scripts: {'exists' if record.get('scripts_dir') else 'missing'}")
        output_fn(f"description: {record.get('description', '')}")
        output_fn(f"when_to_use: {record.get('when_to_use') or 'none'}")
        output_fn(f"allowed tools: {', '.join(record.get('allowed_tools') or []) or 'none'}")
        output_fn(f"frontmatter: {record.get('raw_frontmatter') or {}}")
        warnings = record.get("security_warnings") or []
        output_fn(f"warnings: {', '.join(warnings) if warnings else 'none'}")
        output_fn(f"allowed_builtin_skills: {', '.join(record.get('allowed_builtin_skills') or []) or 'none'}")
        output_fn(f"input_schema: {record.get('input_schema') or {}}")
        errors = record.get("validation_errors") or []
        output_fn(f"validation_errors: {', '.join(errors) if errors else 'none'}")
        return True
    output_fn("Usage: /skills list | /skills inspect <name> | /skills reload | /skills paths | /skills selected | /skills enable <name> | /skills disable <name>")
    return True


def _skill_records(session: ShellSession, registry) -> list[dict]:
    records = []
    for skill in registry.describe():
        records.append(
            {
                "name": skill["name"],
                "type": skill.get("type", "builtin"),
                "source": skill.get("source", "built-in"),
                "description": skill.get("description", ""),
                "when_to_use": skill.get("when_to_use"),
                "allowed_builtin_skills": [],
                "input_schema": skill.get("input_schema", {}),
                "enabled": skill.get("enabled", True),
                "path": skill.get("path"),
                "validation_errors": skill.get("validation_errors", []),
                "claude_compatible": skill.get("claude_compatible", False),
                "skill_root": skill.get("skill_root"),
                "skill_md_path": skill.get("skill_md_path"),
                "assets_dir": skill.get("assets_dir"),
                "references_dir": skill.get("references_dir"),
                "scripts_dir": skill.get("scripts_dir"),
                "raw_frontmatter": skill.get("raw_frontmatter", {}),
                "allowed_tools": skill.get("allowed_tools", []),
                "security_warnings": skill.get("security_warnings", []),
            }
        )
    known = {item["name"] for item in records}
    for record in session.user_skill_records:
        if record["name"] not in known:
            records.append(record)
    return sorted(records, key=lambda item: (item.get("source", ""), item["name"]))


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
