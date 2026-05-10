# PPT Agent

PPT Agent is a LangGraph-based CLI scaffold for generating PowerPoint decks through a controlled runtime.

The model layer is responsible for deciding what to do and producing structured specs. Deck file writes are performed only by tools and runtime code.

## Architecture

- `graph`: Agent loop and state transitions.
- `nodes`: Plan, build, QA, and repair steps.
- `prompts`: Layered prompt text instead of one large system prompt.
- `tools`: Capability boundary exposed to the graph.
- `runtime`: Controlled PowerPoint file operations.
- `domain`: Typed state and deck specs.
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
```

## Document-to-Deck MVP

PPT Agent can generate a traceable PPTX from document evidence. The document flow converts Markdown or parser output into `evidence.json`, creates a schema-versioned `plan.json` with citations where evidence is available, builds a PPTX, and runs deterministic QA/repair on the plan.

Ingest Markdown:

```bash
ppt-agent ingest input.md --out .ppt-agent/evidence.json
```

Ingest a PDF through MinerU:

```bash
ppt-agent ingest paper.pdf --parser mineru --workdir .ppt-agent/parsed --out .ppt-agent/evidence.json
```

Check whether the local MinerU runtime is available:

```bash
ppt-agent doctor
```

The PDF ingest command defaults to MinerU's `pipeline` backend and `auto` method for a local-first CPU-capable path. You can pass MinerU options when needed:

```bash
ppt-agent ingest paper.pdf --parser mineru --mineru-backend pipeline --mineru-method ocr --mineru-lang en --out .ppt-agent/evidence.json
```

Plan from evidence:

```bash
ppt-agent plan --evidence .ppt-agent/evidence.json --spec plan.json
```

Build with evidence-backed figures and source trace:

```bash
ppt-agent build plan.json --evidence .ppt-agent/evidence.json --out deck.pptx
```

Run deterministic Document-to-Deck QA:

```bash
ppt-agent qa plan.json --evidence .ppt-agent/evidence.json --out qa_report.json
```

Repair the plan from QA findings:

```bash
ppt-agent repair plan.json --qa qa_report.json --evidence .ppt-agent/evidence.json --out repaired_plan.json
```

Intermediate files:

- `evidence.json`: structured source evidence extracted from Markdown or parser output, including sections, figures, tables, claims, and source references.
- `plan.json`: schema-versioned deck plan. PlanSpec v2 slides can include `role`, `message`, `layout`, `content.figure_ids`, and `citations`.
- `qa_report.json`: deterministic QA report for document-to-deck checks such as missing citations, missing figure assets, too many bullets, empty messages, and layout/content mismatches.

MinerU is optional. If MinerU is not installed, use Markdown input or pre-created/mock parser output. PDF parsing with `--parser mineru` requires the user to install MinerU separately and make the `mineru` command available on `PATH`.

Current limitations:

- PDF understanding depends on parser output quality and is not guaranteed to work perfectly for every PDF.
- The pipeline does not recover original LaTeX source.
- The system does not automatically verify every factual claim.
- Source trace quality depends on whether the parser provides useful source files, pages, captions, and evidence IDs.

LLM planner setup:

```bash
ppt-agent llm providers
ppt-agent llm configure --provider deepseek --model deepseek-chat
ppt-agent llm set-key deepseek --api-key <your-key>
ppt-agent plan "Quarterly product roadmap" --provider deepseek --model deepseek-chat --spec plan.json
```

## Current Scope

The planner can use a configured LLM provider or fall back to the deterministic planner when no provider/model is configured. Artifact generation, validation, migration, build, and runtime writes remain deterministic and controlled by code.

## Memory Policy

Project memory stores durable project preferences, accepted outputs, and failure patterns. See [docs/memory-policy.md](docs/memory-policy.md) for what may be remembered, what must not be remembered, and how workspace-scoped long-term memory is isolated and governed.

The reusable PostgreSQL/pgvector long-term memory library is installed from GitHub as `agent-long-memory` and imported as `agent_long_memory`. Install PPT Agent with the `long-term-memory` extra to pull it from `https://github.com/corrie2/Tools`; records are isolated by resolved workspace scope before any vector search is run.

