# Multi-Agent PPT Pipeline

The default deck planning path is being refactored around a Supervisor / Worker pipeline implemented as a LangGraph `StateGraph`. Each Agent is represented as a graph node.

## Default Flow

```text
DeckIntent
  -> supervisor_start node creates the request context
  -> brief_outline node writes brief.json and outline.json
  -> brief_outline_eval node evaluates brief and outline
  -> content node writes content.json
     || design_chart node writes design_chart.json
  -> join waits for content.json and design_chart.json
  -> content_eval node evaluates content
  -> design_chart_eval node evaluates design and chart output
  -> supervisor_merge node merges artifacts into slides_ir.json
  -> slides_ir_eval node evaluates the merged IR
  -> qa node writes review_report.json
  -> supervisor_repair node repairs slides_ir when required
  -> page_designer node writes page_design.json
  -> renderer_engineer node inspects renderer code and writes renderer_engineer_report.json
  -> page_generator node converts slides_ir + page_design into PptSpec
  -> render_review node writes render_review_report.json
  -> final_eval node evaluates final QA and render review results
  -> Existing PPTX renderer builds the deck
  -> visual_quality_evaluator reviews the generated PPTX and writes *.visual_quality_report.json
```

The first implementation keeps deterministic fallbacks so the data contract and normal CLI path remain stable when model calls fail. The graph nodes can use LLM-backed workers when keys are configured, and each stage keeps isolated artifact ownership. After `brief_outline_eval` passes, `content` and `design_chart` run as parallel LangGraph branches because both depend only on `brief.json` and `outline.json`; evaluation and `supervisor_merge` stay ordered so rework and artifact merging remain deterministic.

## Task Directory

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

## Ownership

Only the owning agent should write each artifact:

| Artifact | Owner |
|---|---|
| `user_request.json` | Supervisor |
| `task_plan.json` | Supervisor |
| `brief.json` | Brief + Outline Agent |
| `outline.json` | Brief + Outline Agent |
| `content.json` | Content Agent |
| `design_chart.json` | Design + Chart Agent |
| `slides_ir.json` | Supervisor |
| `review_report.json` | QA Agent |
| `evaluation_report.json` | Evaluator Agent |
| `page_design.json` | Page Designer Agent |
| `renderer_engineer_report.json` | Renderer Engineer Agent |
| `renderer_scripts/` | Renderer Engineer Agent |
| `render_review_report.json` | Render Review Agent |

Workers exchange data only through JSON artifacts. They do not mutate each other's outputs.

## Skill Governance

Imported skills still live under the existing skill locations, especially:

```text
.ppt-agent/skills/
```

The multi-agent rule is:

```text
Supervisor can read every enabled skill.
Supervisor decides which skills each worker may use.
Worker agents receive only their assigned skill context.
Page Designer may receive Supervisor-assigned design/layout skills.
Renderer Engineer may receive Supervisor-assigned renderer/code/script skills and may inspect renderer code.
Page Generator receives no content/style skill context.
```

Skill assignment is written into `task_plan.json`:

```json
{
  "skill_policy": {
    "supervisor_can_read_all_skills": true,
    "workers_can_use_only_assigned_skills": true,
    "page_designer_can_use_design_skills": true,
    "renderer_engineer_can_read_renderer_code": true,
    "renderer_engineer_can_propose_scripts": true,
    "page_generator_uses_skills": false
  },
  "skill_assignments": {
    "brief_outline": ["business-report"],
    "content": ["executive-writing"],
    "design_chart": ["business-tech-style"],
    "qa": ["deck-quality-check"],
    "page_designer": ["editorial-layout-skill"],
    "renderer_engineer": ["pptx-renderer-engineering"],
    "render_review": ["ppt-render-review"],
    "page_generator": []
  }
}
```

There are two ways to control assignment.

1. Add `agent_scope` to a skill manifest:

```json
{
  "name": "executive-writing",
  "description": "Executive-ready slide writing guidance.",
  "type": "markdown",
  "agent_scope": ["content", "qa"]
}
```

2. Override assignments in:

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

If neither is present, Supervisor uses a conservative rule-based assignment from the skill name, description, `when_to_use`, and markdown instructions.

## Data Contract

`slides_ir.json` is the single merge point before page design. Page Designer reads the merged IR and writes `page_design.json`; Renderer Engineer reads `slides_ir.json`, `page_design.json`, and selected renderer code context to write `renderer_engineer_report.json`; Page Generator then reads only `slides_ir.json` plus `page_design.json` and does not read individual worker files directly.

The pipeline validates intermediate artifacts with Pydantic models and writes validation status into `task_plan.json`. QA checks `slides_ir.json` for:

- page count consistency
- continuous slide numbering
- missing titles or messages
- visible bullet density
- missing layouts

## Current Defaults

- Default language: `zh-CN`
- Default page count: `15`
- Default style: business, clear, technology-oriented
- Default command path: normal `plan` / `run`
- Evidence-backed planning still uses the existing evidence planner to preserve citations and figure references.

## Default Model Routing

Agent model routing lives in:

```text
.ppt-agent/agents/config.json
```

The default routing is:

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

The Page Generator stays deterministic. It converts `slides_ir.json` plus `page_design.json` into `PptSpec` and then into PPTX-ready data.
Render Review checks the Page Generator mapping and only reports issues; it does not mutate `slides_ir.json`, `PptSpec`, or PPTX.

Use these commands to inspect or write the default config:

```text
ppt-agent agent show-config
ppt-agent agent init-config
```

If a worker model call fails or the DeepSeek API key is missing, the pipeline falls back to the deterministic worker implementation by default.

## Evaluation And Rework

Evaluator nodes gate the most important stages:

- `brief_outline_eval`
- `content_eval`
- `design_chart_eval`
- `slides_ir_eval`
- `final_eval`

The Evaluator only writes `evaluation_report.json`; it does not mutate worker artifacts. `content` and `design_chart` are produced in parallel, then the graph joins before running `content_eval`, `design_chart_eval`, and `supervisor_merge`. This keeps the expensive worker generation parallel while avoiding concurrent writes to `evaluation_report.json` and `transitions`. Rule checks run first; the LLM Evaluator is called only when rule checks find warning/error conditions. A stage can be sent back to the responsible node when:

- `severity = error`
- `requires_rework = true`
- `score < 0.75`

Each evaluation stage can trigger at most one local rework attempt. After that, remaining issues stay in `evaluation_report.json` for Supervisor and user review.

## Next Extension Points

1. Add explicit graph-level streaming status for each node.
2. Add another safe parallel branch for non-mutating QA/page-design prechecks after `slides_ir.json`.
3. Persist web session and run status for PPT Agent Studio instead of keeping web sessions in memory only.
4. Add SSE/WebSocket event streaming for the existing web UI and future React/Vite frontend.
5. Add a second QA pass after PPTX rendering when the user requests testing/verification.
