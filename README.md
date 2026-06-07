# PPT Agent

PPT Agent is a multi-agent CLI runtime for generating PowerPoint decks through a controlled, schema-driven pipeline.

The default `plan` / `run` path now uses a Supervisor / Worker architecture. Worker agents produce structured JSON artifacts, the Supervisor makes decisions, assigns skills, merges results, and handles repair, while PowerPoint file writes are performed only by deterministic runtime code.

Chinese documentation: [README.zh.md](README.zh.md)

## Architecture

- `runtime/multi_agent_pipeline.py`: Multi-agent LangGraph deck planning pipeline; each Agent is represented as a graph node.
- `runtime/harness/`: Task manifest, stage archive, event log, quality gate, and resume metadata for Harness-style runs.
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
ppt-agent run "Teach Transformer attention" --skill-template course-teaching-deck --out deck.pptx
ppt-agent serve
ppt-agent task list
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
- Harness task progress, including current stage, completed stage count, and manifest links

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
  -> design_chart node creates design_chart.json
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
  manifest.json
  input/
    user_request.json
  logs/
    events.jsonl
  stages/
    {order}_{stage_id}/
      input.json
      output.json
      eval.json
      status.json
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
    ppt_spec.json
  task_plan.json
```

The default page count is `15`. Each Agent is a LangGraph node. Workers collaborate through JSON artifacts and do not mutate each other's outputs. The Harness archive keeps per-stage input, output, evaluation, status, event, and resume metadata so a later UI or CLI command can inspect progress and continue from existing intermediate artifacts.

Evaluator sidecar nodes run immediately after key artifacts are produced. Rule checks run first; the LLM Evaluator is called only when rule checks find warning/error conditions. A stage is sent back to the responsible node when the evaluation has `severity = error`, `requires_rework = true`, or `score < 0.75`. Each evaluation stage can trigger at most one local rework attempt.

## Harness Tasks And Recovery

Harness task commands inspect and manage archived multi-agent runs under `.ppt-agent/tasks/`:

```bash
ppt-agent task list
ppt-agent task inspect <task-id-or-path>
ppt-agent task events <task-id-or-path> --limit 50
ppt-agent task artifacts <task-id-or-path>
ppt-agent task continue <task-id-or-path>
ppt-agent task continue <task-id-or-path> --auto-rework --max-rework 1
ppt-agent task approve <task-id-or-path> --stage plan_confirm --note "approved"
ppt-agent task reject <task-id-or-path> --stage plan_confirm --reason "revise outline"
ppt-agent task gates <task-id-or-path> --stage content
ppt-agent task preview <task-id-or-path>
ppt-agent task resume <task-id-or-path> --out resumed_plan.json
ppt-agent task retry-stage <task-id-or-path> content --reason "update required"
```

Key behaviors:

- `manifest.json` records task status, current stage, stage outputs, reports, final `ppt_spec`, and resume hints.
- `logs/events.jsonl` records stage-level progress events for process views.
- Stage folders keep `input.json`, `output.json`, `eval.json`, and `status.json` snapshots.
- Quality gates run deterministic checks before expensive LLM evaluation where possible.
- Confirmation stages (`plan_confirm`, `build_confirm`) can pause the task until a user approves or requests changes.
- Page preview writes lightweight per-slide JSON and HTML previews under `previews/` from the archived `ppt_spec`.
- `retry-stage` followed by `continue` can regenerate `content` and downstream stages from archived `brief_outline` artifacts. It reuses the existing LLM-capable pipeline nodes when agent LLM config is enabled, and falls back deterministically when LLM calls are unavailable.
- `--auto-rework` enables bounded quality-gate retry attempts; manual review remains the default.
- `resume` exports the latest archived `ppt_spec` back into a normal plan file for build or review.

Studio exposes the same data through read-only task APIs:

```text
GET /api/sessions/{session_id}/tasks
GET /api/sessions/{session_id}/tasks/{task_id}
GET /api/sessions/{session_id}/tasks/{task_id}/events
GET /api/sessions/{session_id}/tasks/{task_id}/artifacts
POST /api/sessions/{session_id}/tasks/{task_id}/continue
POST /api/sessions/{session_id}/tasks/{task_id}/approve
POST /api/sessions/{session_id}/tasks/{task_id}/reject
POST /api/sessions/{session_id}/tasks/{task_id}/gates
```

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
ppt-agent skill init-template academic-paper-deck
ppt-agent skill init-template course-teaching-deck
ppt-agent skill init-template business-report-deck
ppt-agent skill init-template deck-quality-gate
```

Use a template during planning or running:

```bash
ppt-agent plan "Explain retrieval augmented generation" --skill-template course-teaching-deck --spec plan.json
ppt-agent run "Board-level AI adoption report" --skill-template business-report-deck --out output/report.pptx
```

If the template skill does not exist yet, PPT Agent creates it under `.ppt-agent/skills/` and adds its skill name to `DeckIntent.applied_skills`, so the Supervisor can route it to the relevant worker agents.

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

Skill manifests can also describe v2 governance metadata:

```json
{
  "applies_to": ["paper", "course", "business"],
  "quality_gates": ["citation_required_when_evidence", "max_bullets_per_slide"],
  "artifacts": {"qa_rules": "qa_rules.json"},
  "examples": ["examples/request.json"],
  "version": "2.0.0"
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

## Feedback Capture

Use feedback commands to preserve accepted outputs, project preferences, corrections, and failure patterns for later planning or review:

```bash
ppt-agent feedback add "Prefer page-level preview before full PPTX build" --type preference
ppt-agent feedback add "Content stage missed citations" --type failure --task <task-id> --stage content
ppt-agent feedback accept <task-id> --note "Approved as final sales deck style"
```

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

### Optional MinerU GPU Acceleration

PPT Agent keeps CUDA-specific packages out of the default project dependencies. MinerU can run on CPU, and GPU acceleration depends on the user's operating system, NVIDIA driver, Python version, and CUDA-enabled PyTorch build.

When PPT Agent Studio starts with `ppt-agent serve`, it starts and warms a local `mineru-api` service by default. PDF parsing reuses that service instead of starting a fresh MinerU process for each file. The runtime checks the current Python environment and asks MinerU to use CUDA when `torch.cuda.is_available()` is true; otherwise it falls back to CPU.

Check the active device in your environment:

```bash
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"
```

For NVIDIA GPU acceleration, install the CUDA-enabled PyTorch build that matches your machine from the official PyTorch selector:

```text
https://pytorch.org/get-started/locally/
```

Do not blindly copy another machine's CUDA install command. If you need to force a device for debugging, set:

```bash
PPT_AGENT_MINERU_DEVICE=cuda
```

Useful Studio startup options:

```bash
ppt-agent serve
ppt-agent serve --no-preload-mineru
ppt-agent serve --mineru-port 8010
```

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
