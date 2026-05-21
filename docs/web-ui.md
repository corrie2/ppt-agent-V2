# PPT Agent Studio Web UI

PPT Agent Studio is the local web interface for PPT Agent. It is designed as a visual workbench over the same runtime used by the CLI, not as a separate implementation.

## Goals

- Keep CLI and Web behavior aligned.
- Reuse `ShellSession`, `SkillRegistry`, `ChatAgent`, plan generation, QA, and build skills.
- Make the current workspace, draft request, plan, pending build, and generated artifacts visible.
- Keep skill selection automatic by default, with session-level disable/enable controls for advanced users.

## Start The Server

```bash
pip install -e .
ppt-agent serve
```

Default URL:

```text
http://127.0.0.1:7860
```

Options:

```bash
ppt-agent serve --host 127.0.0.1 --port 7860
ppt-agent serve --reload
```

## Architecture

```text
CLI commands
    |
    |        Web routes + embedded UI
    |        src/ppt_agent/server/app.py
    |             |
    v             v
Shared application service
src/ppt_agent/app/web_service.py
    |
    v
Existing runtime
ShellSession, SkillRegistry, ChatAgent, generate_plan, QA, build_ppt, build_html_deck
```

The web service keeps in-memory sessions. Each session owns:

- a `ShellSession`
- a `SkillRegistry`
- a `ChatAgent`
- a rolling event log

The first implementation is local and process-bound. Restarting the server clears active web sessions, but generated files remain in the workspace.

## Page Capabilities

The embedded page at `/` provides:

- workspace scanning
- file list display
- natural-language request entry
- event log display
- draft request preview
- skill availability display
- per-session skill disable/enable toggles
- plan generation
- pending build approval
- latest plan JSON preview
- output artifact links

## Main API

Create a session:

```http
POST /api/sessions
```

Request body:

```json
{
  "cwd": null,
  "assistant_enabled": true
}
```

Read session state:

```http
GET /api/sessions/{session_id}/state
```

Scan files:

```http
POST /api/sessions/{session_id}/workspace/scan?max_depth=3
```

Send a natural-language request:

```http
POST /api/sessions/{session_id}/chat
```

Request body:

```json
{
  "message": "Use SIEVE.pdf to make a 20+ slide graduate teaching deck in magazine-style HTML."
}
```

Generate a plan directly from the current draft or supplied arguments:

```http
POST /api/sessions/{session_id}/plans
```

Read the latest plan:

```http
GET /api/sessions/{session_id}/plans/latest
```

Approve a pending build:

```http
POST /api/sessions/{session_id}/builds/approve
```

List skills:

```http
GET /api/sessions/{session_id}/skills
```

Disable or enable a user skill for the current web session:

```http
POST /api/sessions/{session_id}/skills/{skill_name}/disable
POST /api/sessions/{session_id}/skills/{skill_name}/enable
```

List generated artifacts:

```http
GET /api/sessions/{session_id}/artifacts
```

Open an artifact:

```http
GET /api/sessions/{session_id}/artifact?path=<absolute-path>
```

Artifact access is limited to the session input directory, output directory, and `.ppt-agent` workspace data.

## Skill Behavior

Project user skills are loaded into the session as available capabilities. The web UI does not ask the user to choose skills at startup.

The agent decides whether to apply a skill based on the user's request. For example, a request for a magazine-style single HTML deck can apply `guizang-ppt-skill`, while a standard PPTX request should not apply it automatically.

Skill toggles in the web UI are session-level controls:

- enabled: the skill is available for routing
- disabled: the skill is hidden from routing and explicit requests for it are blocked

## Current Limitations

- Web sessions are in memory only.
- There is no user authentication.
- Long-running actions currently return when complete; SSE/WebSocket streaming can be added later.
- The page is embedded in the FastAPI module for the MVP. A future React/Vite frontend can replace it while keeping the same service layer.
- The CLI remains the source of truth for scripted automation and batch usage.

## Extension Points

Recommended next steps:

1. Add persistent web session storage under `.ppt-agent/web/`.
2. Add SSE for live pipeline events.
3. Add upload support for files into `input/`.
4. Add richer HTML/PPTX previews.
5. Move the embedded page into a dedicated frontend app once the API stabilizes.
