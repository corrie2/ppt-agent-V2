from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ppt_agent.storage.llm_settings import load_api_key, load_selection
from ppt_agent.storage.project_memory import ensure_project_memory


DEFAULT_ASSISTANT_PROVIDER = "deepseek"
DEFAULT_ASSISTANT_MODEL = "deepseek-v4-flash"


@dataclass
class PendingAction:
    skill_name: str
    arguments: dict
    description: str


@dataclass
class PendingUserRequest:
    text: str
    requested_source_names: list[str] = field(default_factory=list)
    topic: str | None = None
    slides: int | None = None
    min_slides: int | None = None
    audience: str | None = None
    tone: str | None = None


@dataclass
class DraftPptRequest:
    topic: str | None = None
    audience: str | None = None
    tone: str | None = None
    min_slides: int | None = None
    slide_count: int | None = None
    requested_pdf_name: str | None = None
    requested_pdf_index: int | None = None
    selected_sources: list[str] = field(default_factory=list)
    exclude_other_sources: bool = False
    output_format: str = "pptx"
    applied_skills: list[str] = field(default_factory=list)
    theme: str | None = None
    skill_root: str | None = None
    skill_md_path: str | None = None

    def merge(self, values: dict) -> None:
        if values.get("slides") and not values.get("slide_count"):
            values = dict(values)
            values["slide_count"] = values["slides"]
        for key in ("topic", "audience", "tone", "requested_pdf_name", "output_format", "theme", "skill_root", "skill_md_path"):
            value = values.get(key)
            if value:
                setattr(self, key, value)
        for key in ("min_slides", "slide_count", "requested_pdf_index"):
            value = values.get(key)
            if value:
                setattr(self, key, int(value))
        if values.get("selected_sources"):
            self.selected_sources = list(values["selected_sources"])
        if values.get("exclude_other_sources"):
            self.exclude_other_sources = True
        if values.get("applied_skills"):
            for skill in values["applied_skills"]:
                if skill not in self.applied_skills:
                    self.applied_skills.append(skill)

    def to_generate_plan_arguments(self, fallback_sources: list[str] | None = None) -> dict:
        sources = self.selected_sources or list(fallback_sources or [])
        return {
            "topic": self.topic or self.requested_pdf_name or "PPT",
            "sources": sources,
            "audience": self.audience,
            "tone": self.tone,
            "min_slides": self.min_slides,
            "slides": self.slide_count,
            "output_format": self.output_format,
            "applied_skills": self.applied_skills,
            "theme": self.theme,
            "skill_root": self.skill_root,
            "skill_md_path": self.skill_md_path,
        }



@dataclass
class AgentLoopState:
    messages: list[dict] = field(default_factory=list)
    pending_user_request: str | None = None
    last_skill_result: dict | None = None
    turn_count: int = 0
    transition: str | None = None
    terminal_reason: str | None = None
    needs_user_input: bool = False
    needs_approval: bool = False


@dataclass
class ShellSession:
    cwd: Path
    input_dir: Path
    output_dir: Path
    discovered_sources: list[dict] = field(default_factory=list)
    selected_sources: list[str] = field(default_factory=list)
    latest_plan_path: str | None = None
    latest_plan_sources: list[str] = field(default_factory=list)
    latest_ppt_path: str | None = None
    latest_html_path: str | None = None
    last_build_status: str | None = None
    current_request: str | None = None
    assistant_enabled: bool = False
    assistant_provider: str | None = None
    assistant_model: str | None = None
    pending_action: PendingAction | None = None
    pending_user_request: PendingUserRequest | None = None
    draft_request: DraftPptRequest = field(default_factory=DraftPptRequest)
    last_loop_state: AgentLoopState = field(default_factory=AgentLoopState)
    user_skill_records: list[dict] = field(default_factory=list)
    available_user_skills: list[str] = field(default_factory=list)
    enabled_user_skills: list[str] = field(default_factory=list)
    recent_messages: list[dict] = field(default_factory=list)
    active_skill_context: str | None = None
    active_skill_name: str | None = None

    @classmethod
    def create(cls, cwd: Path | None = None) -> "ShellSession":
        root = (cwd or Path.cwd()).resolve()
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        ensure_project_memory(root)

        selection = load_selection(root)
        provider = selection.provider if selection else DEFAULT_ASSISTANT_PROVIDER
        model = selection.model if selection else DEFAULT_ASSISTANT_MODEL
        return cls(
            cwd=root,
            input_dir=input_dir,
            output_dir=output_dir,
            assistant_provider=provider,
            assistant_model=model,
        )

    def remember_message(self, role: str, content: str) -> None:
        self.recent_messages.append({"role": role, "content": content})
        self.recent_messages = self.recent_messages[-12:]

    def set_input_dir(self, path: Path) -> None:
        self.input_dir = path.resolve()
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.discovered_sources = []
        self.selected_sources = []
        self.draft_request.selected_sources = []

    def set_output_dir(self, path: Path) -> None:
        self.output_dir = path.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def selected_pdf_paths(self) -> list[str]:
        pdf_paths = {item["path"] for item in self.discovered_sources if item["file_type"] == "pdf"}
        selected = self.selected_sources or self.draft_request.selected_sources
        if not pdf_paths:
            return [path for path in selected if Path(path).suffix.lower() == ".pdf"]
        return [path for path in selected if path in pdf_paths]

    def selected_pdf_names(self) -> list[str]:
        return [Path(path).name for path in self.selected_pdf_paths()]

    def enable_assistant(self) -> None:
        selection = load_selection(self.cwd)
        if selection:
            self.assistant_provider = selection.provider
            self.assistant_model = selection.model
        elif not self.assistant_provider or not self.assistant_model:
            self.assistant_provider = DEFAULT_ASSISTANT_PROVIDER
            self.assistant_model = DEFAULT_ASSISTANT_MODEL
        self.assistant_enabled = True

    def disable_assistant(self) -> None:
        self.assistant_enabled = False

    def assistant_key_configured(self) -> bool:
        if not self.assistant_provider:
            return False
        return load_api_key(self.assistant_provider, self.cwd) is not None

    def mode_label(self) -> str:
        return "ai assistant" if self.assistant_enabled else "manual cli"
