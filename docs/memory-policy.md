# Memory Policy

This project supports two memory stores:

- Local project memory in `.ppt-agent/memory/*.json` and `.jsonl`.
- Optional PostgreSQL long-term semantic memory, enabled only when `PPT_AGENT_VECTOR_MEMORY=1` and `PPT_AGENT_MEMORY_DATABASE_URL` is set.

The PostgreSQL/pgvector long-term memory implementation is installed from GitHub as `agent-long-memory` and imported as `agent_long_memory` so other projects can use it without depending on PPT Agent runtime modules. The `ppt_agent.storage.memory_*` modules are compatibility aliases for that package.

The local files remain the compatibility baseline. PostgreSQL memory is workspace-scoped long-term memory used for retrieval and semantic search when enabled. If PostgreSQL read or write fails, the system must fall back to the local files.

## What Can Be Remembered

Store information only when it is useful for future work in the same project and is not sensitive beyond the project boundary.

Allowed long-term memory includes:

- User presentation preferences, such as preferred style, audience level, tone, density, visual constraints, and recurring dislikes.
- Project-specific deck conventions, such as naming, preferred output format, visual style, slide structure, or accepted patterns.
- Accepted outputs and durable execution traces that help reproduce successful project workflows.
- QA failures and repair patterns that are likely to prevent repeated mistakes in the same project.
- Stable source or module references that help locate project material, such as a relative module path, artifact name, or non-sensitive source reference.
- Technical facts about this project that are explicitly provided by the user or observed from project files.

Memory records should be concise. Prefer storing the durable rule or fact, not the full conversation that produced it.

## What Must Not Be Remembered

Do not store information that is not necessary for future project work, or that creates avoidable privacy, security, or compliance risk.

Forbidden long-term memory includes:

- Secrets, credentials, API keys, access tokens, passwords, private keys, session cookies, or authentication headers.
- Personal data that is not necessary for project execution, including government identifiers, payment data, home addresses, phone numbers, private email addresses, health data, biometric data, or sensitive demographic attributes.
- Confidential third-party data unless the user explicitly instructs that it belongs in this project memory and it is necessary for the task.
- Raw proprietary documents, full source files, full slide decks, full PDFs, or large copied passages. Store references, summaries, or derived preferences instead.
- Temporary user intent, one-off instructions, jokes, speculation, or content that is unlikely to be useful later.
- Cross-project assumptions. A preference from one project must not be promoted into another project unless the user explicitly asks for that.
- Harmful operational instructions, such as instructions to bypass approvals, disable safety checks, exfiltrate data, or hide changes from review.

If a record mixes useful durable information with forbidden content, store only a sanitized summary.

## Project Scope Isolation

Every long-term memory record must belong to exactly one workspace scope.

Workspace scope is resolved by:

1. Finding the Git repository root for the workspace when available.
2. Falling back to the resolved workspace path when Git root detection is unavailable.
3. Recording the project name, root path, and optional Git remote in `memory_projects`.

PostgreSQL retrieval must filter by `project_id` before applying vector similarity search. This prevents memories from different repositories or workspaces from being mixed by semantic similarity.

The local memory path is also project-local:

```text
<workspace>/.ppt-agent/memory/
```

Local and PostgreSQL memory must not be treated as global user memory. Do not use records from another `root_path` or `project_id` unless the user explicitly requests migration or import.

For external codebases, install `agent-long-memory` from `https://github.com/corrie2/Tools`, then use `agent_long_memory.resolve_memory_scope(workspace)` and the semantic memory APIs with the target workspace path. Do not hard-code a shared `project_id`; let the library create one per Git root or workspace root.

## Write Policy

Local JSON/JSONL writes are the source of compatibility and must happen first.

When vector memory is enabled:

1. Write the existing local JSON/JSONL record.
2. Attempt to write a PostgreSQL semantic memory record.
3. Attempt to write its embedding.
4. Return long-term memory status in the operation result when applicable.

PostgreSQL write failure must not roll back or block the local write. Return a failed long-term memory status and continue.

## Read Policy

When vector memory is enabled and configured:

1. Try PostgreSQL semantic memory first.
2. For non-empty queries, use vector search filtered by `project_id` and memory type.
3. For empty queries, list recent valid records filtered by `project_id` and memory type.
4. If PostgreSQL read fails for any reason, fall back to the local JSON/JSONL files.

The public return shape of `retrieve_project_memory` and `retrieve_failure_patterns` should remain compatible with existing callers.

## Governance

Memory records should support lifecycle management through these fields:

- `memory_type`: the category of memory, such as `user_preference`, `qa_failure`, or `accepted_output`.
- `source_type` and `source_ref`: where the memory came from.
- `tags`: lightweight labels for filtering and governance.
- `importance` and `confidence`: values from `0` to `1`.
- `valid_until`: optional expiration for records that should age out.
- `superseded_by`: optional pointer to a newer record that replaces an older one.
- `metadata`: optional structured context. Do not store secrets or raw sensitive data here.

Deletion and correction should be workspace-scoped:

- To delete a project's PostgreSQL memory, delete its row from `memory_projects`; dependent records and embeddings cascade.
- To remove one record, delete from `memory_records` by `id` and `project_id`.
- To correct a record, create a replacement and set `superseded_by` on the older record when possible.
- To disable PostgreSQL memory, unset `PPT_AGENT_VECTOR_MEMORY` or set it to a value other than `1`.

## Review Checklist

Before adding a new memory write path, verify:

- The record is useful for future work in the same project.
- The record does not contain secrets or unnecessary personal data.
- The record is concise and preferably derived, not a raw document copy.
- The write happens after local JSON/JSONL compatibility storage succeeds.
- PostgreSQL failure cannot break the user workflow.
- Reads are filtered by workspace scope before semantic similarity.
- Tests cover disabled, enabled, and fallback behavior.

