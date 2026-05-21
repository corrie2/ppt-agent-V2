# PPT Agent

PPT Agent is a multi-agent CLI runtime for generating PowerPoint decks through a controlled, schema-driven pipeline.

The default `plan` / `run` path now uses a Supervisor / Worker architecture. Worker agents produce structured JSON artifacts, the Supervisor makes decisions, assigns skills, merges results, and handles repair, while PowerPoint file writes are performed only by deterministic runtime code.

Chinese documentation: [README.zh.md](README.zh.md)

## Architecture

- `runtime/multi_agent_pipeline.py`: Multi-agent LangGraph deck planning pipeline; each Agent is represented as a graph node.
- `runtime/renderer_engineer.py`: Renderer Engineer Agent logic, renderer-code context extraction, extension planning, and task-local helper script output.
- `runtime/visual_quality.py`: Visual Quality Evaluator for post-build PPTX metrics, LLM visual assessment, and `.visual_quality_report.json` output.
- `runtime/agent_llm.py`: Per-agent provider/model routing and JSON-only LLM calls.
- `runtime/agent_skills.py`: Supervisor-owned skill catalog and worker skill assignment.
- `app/web_service.py`: Shared application service used by the web UI to reuse shell sessions, skill registry, planning, and build actions.
- `server/app.py`: FastAPI server and embedded PPT Agent Studio web UI.
- `graph`: Existing agent loop and state transitions.
- `nodes`: Plan, build, QA, repair, and asset nodes.
- `runtime`: Controlled PowerPoint file operations.
- `domain`: Typed state, deck specs, and domain models.
- `storage`: Workspace persistence.

## Quick Start

```bash
pip install -e .
ppt-agent run "Quarterly product roadmap" --out deck.pptx
```

Useful commands:

```bash
ppt-agent plan "Quarterly product roadmap" --spec plan.json
ppt-agent build plan.json --out deck.pptx
ppt-agent run "Quarterly product roadmap" --mode plan
ppt-agent serve
```

For normal non-evidence decks, the default `plan` / `run` path uses the multi-agent pipeline.

## CLI And Web UI

PPT Agent supports two user interfaces over the same local runtime:

- CLI: `ppt`, `ppt-agent plan`, `ppt-agent build`, `ppt-agent run`, and related automation commands.
- Web UI: `ppt-agent serve`, which starts PPT Agent Studio for visual workspace control.

Start the web UI:

```bash
ppt-agent serve
```

Default URL:

```text
http://127.0.0.1:7860
```

PPT Agent Studio is a local visual workbench. It reuses the same `ShellSession`, `SkillRegistry`, planner, QA, and build skills as the CLI. The first version includes:

- workspace file scanning
- natural-language request input
- structured draft request preview
- user skill visibility and session-level enable/disable controls
- plan generation
- pending build approval
- plan JSON preview
- generated artifact list with local artifact links
- event log for agent and skill progress

The web UI does not replace the CLI. Both entrypoints should share behavior through the service layer so planning, skill use, QA, and build logic do not drift.

See [docs/web-ui.md](docs/web-ui.md) for API details and implementation notes.

## Multi-Agent PPT Pipeline

Default flow:

```text
DeckIntent
  -> supervisor_start node creates the request context
  -> brief_outline node creates brief.json and outline.json
  -> brief_outline_eval node evaluates brief and outline
  -> content node creates content.json
     || design_chart node creates design_chart.json
  -> join waits for content.json and design_chart.json
  -> content_eval node evaluates content
  -> design_chart_eval node evaluates design and chart output
  -> supervisor_merge node creates slides_ir.json
  -> slides_ir_eval node evaluates the merged IR
  -> qa node creates review_report.json
  -> supervisor_repair node repairs slides_ir when required
  -> page_designer node creates page_design.json
  -> renderer_engineer node inspects renderer code and creates renderer_engineer_report.json
  -> page_generator node converts slides_ir + page_design into PptSpec
  -> render_review node creates render_review_report.json
  -> final_eval node evaluates final QA and render review results
  -> Existing PPTX renderer builds the deck
  -> visual_quality_evaluator reviews the generated PPTX and writes *.visual_quality_report.json
```

Default agents:

| Agent | Responsibility |
|---|---|
| Supervisor | Makes decisions, assigns skills, merges worker artifacts, and handles repair. |
| Brief + Outline | Interprets the user request and creates the deck brief plus a 15-slide outline. |
| Content | Writes slide titles, core messages, bullets, and speaker notes. |
| Design + Chart | Defines theme, layout rules, chart suggestions, and visual direction. |
| QA | Reviews `slides_ir.json` for structure, completeness, and slide-quality risks. |
| Evaluator | Evaluates key Agent outputs, scores quality gates, and recommends targeted rework. |
| Page Designer | Makes page-level layout, hierarchy, density, and figure strategy decisions in `page_design.json`. |
| Renderer Engineer | Inspects Page Generator/PPTX renderer code, checks whether `page_design.json` is implementable, and writes `renderer_engineer_report.json` plus task-local helper scripts when needed. |
| Page Generator | Deterministically converts `slides_ir.json` plus `page_design.json` into `PptSpec`; it does not use an LLM. |
| Render Review | Reviews Page Generator mapping and reports lost fields or blank-slide risks. |

Each run writes artifacts under:

```text
.ppt-agent/tasks/{task_id}/
  input/
    user_request.json
  intermediate/
    brief.json
    outline.json
    content.json
    design_chart.json
  build/
    slides_ir.json
    review_report.json
    evaluation_report.json
    page_design.json
    renderer_engineer_report.json
    renderer_scripts/
    render_review_report.json
  task_plan.json
```

The default page count is `15`. Each Agent is a LangGraph node. Workers collaborate through JSON artifacts and do not mutate each other's outputs.

Evaluator sidecar nodes run immediately after key artifacts are produced. Rule checks run first; the LLM Evaluator is called only when rule checks find warning/error conditions. A stage is sent back to the responsible node when the evaluation has `severity = error`, `requires_rework = true`, or `score < 0.75`. Each evaluation stage can trigger at most one local rework attempt.

## Agent Model Routing

Agent model routing is configured in:

```text
.ppt-agent/agents/config.json
```

Default routing:

| Agent | Provider | Model |
|---|---|---|
| Supervisor | `deepseek` | `deepseek-v4-pro` |
| Brief + Outline | `deepseek` | `deepseek-v4-flash` |
| Content | `deepseek` | `deepseek-v4-flash` |
| Design + Chart | `deepseek` | `deepseek-v4-flash` |
| QA | `deepseek` | `deepseek-v4-flash` |
| Evaluator | `deepseek` | `deepseek-v4-flash` |
| Page Designer | `deepseek` | `deepseek-v4-flash` |
| Renderer Engineer | `deepseek` | `deepseek-v4-pro` |
| Visual Quality Evaluator | `deepseek` | `deepseek-v4-pro` |
| Render Review | `deepseek` | `deepseek-v4-flash` |
| Page Generator | none | none |

Inspect or write the default config:

```bash
ppt-agent agent show-config
ppt-agent agent init-config
```

Set the DeepSeek API key:

```bash
ppt-agent llm set-key deepseek --api-key <your-key>
```

If an agent model call fails or the API key is missing, the pipeline falls back to deterministic worker logic by default.

## Skill Governance

Imported skills still live in existing skill locations, especially:

```text
.ppt-agent/skills/
```

Common skill commands:

```bash
ppt-agent skill add <path-or-git-url>
ppt-agent skill list
ppt-agent skill validate <path-or-name>
```

Multi-agent skill policy:

```text
Supervisor can read every enabled skill.
Supervisor decides which skills each worker may use.
Worker agents receive only their assigned skill context.
Page Designer may receive design/layout skills assigned by Supervisor.
Renderer Engineer may receive renderer/code/script skills assigned by Supervisor.
Page Generator receives no content/style skill context.
```

You can restrict a skill to specific agents by adding `agent_scope` to `skill.json`:

```json
{
  "name": "executive-writing",
  "description": "Executive-ready slide writing guidance.",
  "type": "markdown",
  "agent_scope": ["content", "qa"]
}
```

You can also override assignments in:

```text
.ppt-agent/agents/skills.json
```

Example:

```json
{
  "brief_outline": ["business-report"],
  "content": ["executive-writing"],
  "design_chart": ["business-tech-style"],
  "qa": ["deck-quality-check"],
  "page_designer": ["editorial-layout-skill"],
  "renderer_engineer": ["pptx-renderer-engineering"],
  "render_review": ["ppt-render-review"],
  "page_generator": []
}
```

Each run writes the resolved `skill_policy`, `skill_catalog`, and `skill_assignments` into that task's `task_plan.json`.

## Document-to-Deck

PPT Agent can generate a traceable PPTX from document evidence. The document flow converts Markdown or parser output into `evidence.json`, creates a schema-versioned `plan.json` with citations where evidence is available, builds a PPTX, and runs deterministic QA/repair on the plan.

```bash
ppt-agent ingest input.md --out .ppt-agent/evidence.json
ppt-agent ingest paper.pdf --parser mineru --workdir .ppt-agent/parsed --out .ppt-agent/evidence.json
ppt-agent doctor
ppt-agent plan --evidence .ppt-agent/evidence.json --spec plan.json
ppt-agent build plan.json --evidence .ppt-agent/evidence.json --out deck.pptx
ppt-agent qa plan.json --evidence .ppt-agent/evidence.json --out qa_report.json
ppt-agent repair plan.json --qa qa_report.json --evidence .ppt-agent/evidence.json --out repaired_plan.json
```

Intermediate files:

- `evidence.json`: structured source evidence extracted from Markdown or parser output.
- `plan.json`: schema-versioned deck plan.
- `qa_report.json`: deterministic QA report for document-to-deck checks.

MinerU is optional. If MinerU is not installed, use Markdown input or pre-created parser output.

## LLM Configuration

```bash
ppt-agent llm providers
ppt-agent llm configure --provider deepseek --model deepseek-v4-flash
ppt-agent llm set-key deepseek --api-key <your-key>
ppt-agent plan "Quarterly product roadmap" --provider deepseek --model deepseek-v4-flash --spec plan.json
```

## Current Scope

The default planner uses the multi-agent pipeline for normal decks. Evidence-backed planning still uses the existing evidence planner so citations and figure references remain stable. Artifact generation, schema validation, plan migration, PPT build, and file writes remain controlled by code.

## Memory Policy

Project memory stores durable project preferences, accepted outputs, and failure patterns. See [docs/memory-policy.md](docs/memory-policy.md) for what may be remembered, what must not be remembered, and how workspace-scoped long-term memory is isolated and governed.
