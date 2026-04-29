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

LLM planner setup:

```bash
ppt-agent llm providers
ppt-agent llm configure --provider deepseek --model deepseek-chat
ppt-agent llm set-key deepseek --api-key <your-key>
ppt-agent plan "Quarterly product roadmap" --provider deepseek --model deepseek-chat --spec plan.json
```

## Current Scope

The planner can use a configured LLM provider or fall back to the deterministic planner when no provider/model is configured. Artifact generation, validation, migration, build, and runtime writes remain deterministic and controlled by code.
