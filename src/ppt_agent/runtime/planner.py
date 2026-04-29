from __future__ import annotations

from ppt_agent.domain.models import DeckIntent, PptSpec, SlideSpec
from ppt_agent.llm.planner import LlmConnectionResult, PlannerConfigError, generate_plan_with_llm, test_llm_connection
from ppt_agent.storage.llm_settings import load_api_key, load_selection


def build_plan_spec(
    intent: DeckIntent,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> PptSpec:
    resolved_provider, resolved_model = resolve_planner_selection(provider=provider, model=model)
    if not resolved_provider or not resolved_model:
        return deterministic_plan_spec(intent)

    api_key = load_api_key(resolved_provider)
    if not api_key:
        raise PlannerConfigError(
            f"missing API key for provider {resolved_provider}. Run `ppt-agent llm set-key {resolved_provider} --api-key <key>`."
        )
    return generate_plan_with_llm(
        intent,
        provider=resolved_provider,
        model=resolved_model,
        api_key=api_key,
    )


def resolve_planner_selection(*, provider: str | None, model: str | None) -> tuple[str | None, str | None]:
    if provider and model:
        return provider, model

    saved = load_selection()
    if provider and not model and saved and saved.provider == provider:
        return provider, saved.model
    if saved and not provider and not model:
        return saved.provider, saved.model
    return provider, model


def test_planner_connection(*, provider: str | None = None, model: str | None = None) -> LlmConnectionResult:
    resolved_provider, resolved_model = resolve_planner_selection(provider=provider, model=model)
    if not resolved_provider or not resolved_model:
        raise PlannerConfigError("no provider/model configured. Run `ppt-agent llm configure --provider <provider> --model <model>`.")

    api_key = load_api_key(resolved_provider)
    if not api_key:
        raise PlannerConfigError(
            f"missing API key for provider {resolved_provider}. Run `ppt-agent llm set-key {resolved_provider} --api-key <key>`."
        )

    return test_llm_connection(
        resolved_provider,
        model=resolved_model,
        api_key=api_key,
    )


def deterministic_plan_spec(intent: DeckIntent) -> PptSpec:
    topic = intent.topic.strip()
    if intent.source_digest:
        return _academic_grounded_plan_spec(intent)
    style_tags = _memory_style_tags(intent)
    return PptSpec(
        title=topic,
        audience=intent.audience,
        theme="executive_blue",
        slides=[
            SlideSpec(
                title=topic,
                objective="Introduce the proposal and frame the executive decision required.",
                core_message=f"{topic} should be funded now to improve pipeline quality and seller productivity.",
                bullets=[
                    "Explain why the current revenue motion needs an AI-assisted operating model now.",
                    "Frame the deck around seller efficiency, manager visibility, and conversion lift.",
                    "Set the executive decision required by the end of the presentation.",
                ],
                supporting_points=[
                    "Target audience and expected decision are explicit.",
                    "Commercial urgency is stated up front.",
                    "Deck scope is positioned as a business proposal, not a feature demo.",
                ],
                speaker_notes=f"Open by framing why {topic} matters to {intent.audience}.",
                visual_type="hero_image",
                image_query="executive sales team collaborating with AI dashboard",
                image_prompt="Clean corporate hero image showing a B2B sales leadership team reviewing an AI revenue dashboard in a modern office.",
                image_caption="AI-enabled revenue execution for frontline sales teams.",
                image_rationale="A hero image helps the cover page feel like a formal executive presentation.",
                layout_hint="title_cover",
                style_tags=["executive", "sales", "strategy", *style_tags],
            ),
            SlideSpec(
                title="Why The Current Sales Enablement Model Is Underperforming",
                objective="Diagnose the business problems that justify change.",
                core_message="Enablement content exists, but sellers still lose time searching, tailoring, and following the right playbook in live deals.",
                bullets=[
                    "Reps spend too much time assembling meeting prep and follow-up material manually.",
                    "Managers lack a consistent view of whether guidance is being applied in active opportunities.",
                    "Marketing, enablement, and sales operations create assets, but field adoption is uneven.",
                ],
                supporting_points=[
                    "Productivity drag shows up as slower cycle times.",
                    "Content inconsistency weakens message quality in customer conversations.",
                    "Coaching is reactive because managers see outcomes later than signals.",
                ],
                visual_type="comparison_table",
                layout_hint="comparison_table",
                style_tags=["diagnostic", "operations", "business", *style_tags],
            ),
            SlideSpec(
                title="What The AI Sales Enablement Solution Must Deliver",
                objective="Describe the target-state capabilities in business terms.",
                core_message="The solution should turn scattered guidance into in-workflow recommendations, reusable content, and manager signals.",
                bullets=[
                    "Give sellers account-specific call prep, objection handling, and follow-up support in one workspace.",
                    "Surface role-based playbooks and assets at the right stage of the deal cycle.",
                    "Create a manager view that highlights adoption, risk signals, and coaching opportunities.",
                ],
                supporting_points=[
                    "Frontline usability matters more than a broad feature checklist.",
                    "Recommendations must align to existing sales methodology.",
                    "Reporting should link behavior to commercial outcomes.",
                ],
                visual_type="three_card_summary",
                layout_hint="three_card_summary",
                style_tags=["solution", "platform", "value"],
            ),
            SlideSpec(
                title="Target Operating Model Across Seller, Manager, And Enablement Teams",
                objective="Show how the proposal changes cross-functional workflows.",
                core_message="The program works only when sellers, managers, and enablement operate on one shared loop of guidance, feedback, and improvement.",
                bullets=[
                    "Sellers receive contextual recommendations before meetings, during follow-up, and at proposal time.",
                    "Managers review adoption and coach against a consistent set of signals each week.",
                    "Enablement teams update content based on usage and opportunity-stage feedback.",
                ],
                supporting_points=[
                    "Responsibilities are clear across field, management, and central teams.",
                    "The system becomes part of revenue cadence, not a side tool.",
                    "Feedback closes the gap between content creation and field execution.",
                ],
                visual_type="process_timeline",
                layout_hint="process_timeline",
                style_tags=["operating-model", "workflow", "timeline"],
            ),
            SlideSpec(
                title="Reference Use Cases For High-Impact Deployment",
                objective="Translate the proposal into concrete deployment scenarios.",
                core_message="The first release should focus on moments where AI reduces prep time and improves customer-facing consistency.",
                bullets=[
                    "Meeting preparation for discovery and account review sessions.",
                    "Post-call recap generation with next-step and risk prompts.",
                    "Proposal drafting support for repeatable solution narratives and value framing.",
                ],
                supporting_points=[
                    "Each use case maps to an existing seller pain point.",
                    "Value is visible in both time savings and message quality.",
                    "Use cases can be rolled out incrementally by segment.",
                ],
                visual_type="workspace_photo",
                image_query="sales representative using AI copilot for account planning",
                image_prompt="Professional office scene showing a B2B account executive using an AI assistant on a laptop with CRM and meeting notes visible.",
                image_caption="Priority use cases focus on prep, follow-up, and proposal quality.",
                image_rationale="A realistic work scene helps the audience visualize adoption in the field.",
                layout_hint="two_column_text_image",
                style_tags=["use-case", "field", "execution"],
            ),
            SlideSpec(
                title="Implementation Roadmap For A 90-Day Pilot",
                objective="Lay out a credible delivery plan with milestones.",
                core_message="A focused 90-day pilot can prove adoption, workflow fit, and revenue impact before broader rollout.",
                bullets=[
                    "Weeks 1-3: prioritize seller journeys, content sources, and success metrics.",
                    "Weeks 4-8: launch the pilot cohort, enable managers, and refine prompts and assets.",
                    "Weeks 9-12: measure adoption, pipeline indicators, and manager coaching usage.",
                ],
                supporting_points=[
                    "Pilot scope stays narrow enough to learn quickly.",
                    "Governance and enablement are built into rollout, not deferred.",
                    "Measurement starts with behavior and extends to commercial movement.",
                ],
                visual_type="process_timeline",
                layout_hint="process_timeline",
                style_tags=["roadmap", "pilot", "delivery"],
            ),
            SlideSpec(
                title="Commercial Impact And Success Metrics",
                objective="Define how leadership should evaluate the program.",
                core_message="The business case depends on productivity gains, message quality, and higher-quality pipeline progression.",
                bullets=[
                    "Track seller prep time saved and adoption by stage-critical workflows.",
                    "Measure improvements in follow-up speed, meeting quality, and conversion from discovery to solution fit.",
                    "Review manager coaching engagement and content reuse across teams.",
                ],
                supporting_points=[
                    "Metrics span leading indicators and commercial outcomes.",
                    "Leadership gets an evidence-based basis for scaling the program.",
                    "Measurement design should be agreed before pilot launch.",
                ],
                visual_type="comparison_table",
                layout_hint="comparison_table",
                style_tags=["metrics", "roi", "leadership"],
            ),
            SlideSpec(
                title="Decision, Risks, And Immediate Next Steps",
                objective="Close with a decision-oriented summary.",
                core_message="Leadership should approve a scoped pilot with named owners, success metrics, and governance checkpoints.",
                bullets=[
                    "Approve pilot scope, target teams, and executive sponsor this month.",
                    "Confirm data, content, and workflow owners before build starts.",
                    "Review risks around adoption, content quality, and security in the launch governance pack.",
                ],
                supporting_points=[
                    "Decision framing is explicit and actionable.",
                    "Risks are acknowledged without weakening the recommendation.",
                    "Next steps are immediate, owned, and measurable.",
                ],
                visual_type="three_card_summary",
                layout_hint="three_card_summary",
                style_tags=["decision", "risk", "action"],
            ),
        ],
    )


def _academic_grounded_plan_spec(intent: DeckIntent) -> PptSpec:
    digest = intent.source_digest or {}
    first = (digest.get("sources") or [{}])[0]
    source_id = first.get("source_id") or first.get("name") or "source"
    title = first.get("title") if first.get("title") != "unknown" else intent.topic
    not_provided = "not provided by source"
    abstract = first.get("abstract") or not_provided
    problem = first.get("problem") or not_provided
    method = first.get("method") or first.get("system") or not_provided
    experiments = first.get("experiments") or not_provided
    results = first.get("results") or not_provided
    limitations = first.get("limitations") or not_provided
    outline = [
        ("Title / Motivation", "Motivation is grounded in source evidence, not inferred from the file name.", [abstract]),
        ("Research Problem", "Focus on the actual problem stated or implied by the source digest.", [problem]),
        ("Background", "Establish the reading context using only available source evidence.", [first.get("motivation") or not_provided]),
        ("Key Idea", "Summarize the central method or idea from the source.", [method]),
        ("System Overview", "Describe the system or approach structure only when available.", [first.get("system") or method]),
        ("Method / Algorithm", "Explain the main method and algorithmic flow from evidence.", [method]),
        ("Data Structures", "Describe source-provided data structures or representations.", [first.get("algorithm") or not_provided]),
        ("Workflow", "Break the method into an explainable source-grounded workflow.", [method]),
        ("Experimental Setup", "Restate only experimental setup visible in source evidence.", [experiments]),
        ("Baselines", "List baselines only if the source provides them.", [first.get("baselines") or not_provided]),
        ("Metrics", "List experimental metrics only if the source provides them.", [first.get("metrics") or not_provided]),
        ("Main Results", "Summarize source-confirmed results without inventing metrics.", [results]),
        ("Ablation / Sensitivity", "Discuss ablation or sensitivity only if source evidence exists.", [first.get("ablation") or not_provided]),
        ("Discussion", "Discuss implications and boundaries grounded in results and limitations.", [results, limitations]),
        ("Limitations", "State source-provided or explicitly unknown limitations.", [limitations]),
        ("Takeaways", "Extract takeaways from the method and results evidence.", [method, results]),
        ("Reading Questions", "Provide discussion questions that do not add unsupported facts.", ["What assumptions does the method rely on?", "What extra evidence would strengthen the evaluation?"]),
        ("Appendix: Terms", "Explain only terms supported by the source digest.", [first.get("terms") or not_provided]),
        ("Appendix: Figure Walkthrough", "Walk through figures or tables only if source evidence mentions them.", [first.get("figures_tables") or not_provided]),
        ("Summary", "Return to the evidence chain across problem, method, and results.", [problem, method, results]),
    ]
    slides = []
    for index, (slide_title, message, bullets) in enumerate(outline, start=1):
        grounded_bullets = [item for item in bullets if item]
        status = "partial" if any("unknown" in item.lower() or "not provided" in item.lower() for item in grounded_bullets) else "grounded"
        slides.append(
            SlideSpec(
                title=slide_title,
                objective=message,
                core_message=message,
                bullets=grounded_bullets,
                visual_type="editorial_diagram" if index not in {1, 20} else "cover" if index == 1 else "summary",
                layout_hint="title_cover" if index == 1 else "two_column_text_image",
                style_tags=["academic", "paper", "grounded", *_memory_style_tags(intent)],
                evidence_refs=[f"{source_id}:digest"],
                grounding_status=status,
                source_notes="Grounded in source_digest and retrieved source chunks; unavailable details are marked as not provided.",
            )
        )
    return PptSpec(
        title=title,
        audience=intent.audience,
        theme="magazine" if intent.output_format == "html" else "executive_blue",
        slides=slides,
        source_digest=digest,
        applied_skills=intent.applied_skills,
        output_format=intent.output_format,
        grounding_warnings=digest.get("warnings", []),
    )


def _memory_style_tags(intent: DeckIntent) -> list[str]:
    tags: list[str] = []
    text = " ".join(
        str(item.get("preference") or item.get("event") or item)
        for item in [*intent.project_preferences, *intent.failure_patterns]
    ).lower()
    if "研究生" in text or "graduate" in text:
        tags.append("graduate-level")
    if "正文太多" in text or "too much text" in text or "文字太多" in text:
        tags.append("concise-copy")
    if "空方框" in text or "empty box" in text or "placeholder" in text:
        tags.append("no-empty-placeholders")
    return tags
