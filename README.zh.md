# PPT Agent

PPT Agent 是一个面向 PowerPoint 自动生成的多智能体 CLI 运行时。它通过结构化 JSON 中间产物组织 PPT 制作流程，由多个专业 Agent 分工完成需求解析、大纲、文案、设计、质检和渲染审查，最终由确定性代码生成 PPT 文件。

默认的普通 `plan` / `run` 路径已经接入 Supervisor / Worker 多智能体流水线。Worker Agent 只生成结构化产物，Supervisor Agent 负责决策、合并、分配 skill 和处理返修，PowerPoint 文件写入只由受控运行时代码完成。

English documentation: [README.md](README.md)

## 架构

- `runtime/multi_agent_pipeline.py`：基于 LangGraph 的多智能体 PPT 规划流水线，每个 Agent 都对应一个 graph node。
- `runtime/harness/`：Harness 风格任务的 manifest、阶段归档、事件日志、质量门和恢复元数据。
- `runtime/renderer_engineer.py`：Renderer Engineer Agent 逻辑、渲染代码上下文提取、扩展方案和任务级辅助脚本输出。
- `runtime/visual_quality.py`：Visual Quality Evaluator，负责 PPTX 生成后的结构化视觉指标、LLM 视觉质量评估和 `.visual_quality_report.json` 输出。
- `runtime/agent_llm.py`：每个 Agent 的 provider/model 路由和 JSON-only LLM 调用。
- `runtime/agent_skills.py`：Supervisor 管理的 skill 目录和 Worker skill 分配。
- `graph`：现有 Agent 循环和状态流转。
- `nodes`：plan、build、QA、repair、asset 等节点。
- `runtime`：受控 PPT 文件操作和运行时逻辑。
- `domain`：类型化状态、PPT spec 和领域模型。
- `storage`：工作区持久化。

## 快速开始

```bash
pip install -e .
ppt-agent run "季度产品路线图" --out deck.pptx
```

常用命令：

```bash
ppt-agent plan "季度产品路线图" --spec plan.json
ppt-agent build plan.json --out deck.pptx
ppt-agent run "季度产品路线图" --mode plan
ppt-agent run "讲解 Transformer attention" --skill-template course-teaching-deck --out deck.pptx
ppt-agent serve
ppt-agent task list
```

对于非 evidence 驱动的普通 PPT，默认 `plan` / `run` 会使用多智能体流水线。

## CLI 和 Web UI

PPT Agent 在同一个本地运行时上提供两种入口：

- CLI：`ppt`、`ppt-agent plan`、`ppt-agent build`、`ppt-agent run` 以及任务、反馈、Skill 等自动化命令。
- Web UI：`ppt-agent serve`，启动本地 PPT Agent Studio，用于可视化管理工作区和生成流程。

启动 Web UI：

```bash
ppt-agent serve
```

默认地址：

```text
http://127.0.0.1:7860
```

PPT Agent Studio 复用 CLI 的 `ShellSession`、`SkillRegistry`、planner、QA 和 build skill。当前包括：

- 工作区文件扫描
- 自然语言需求输入
- 结构化 draft request 预览
- 用户 Skill 可见性和会话级启用/禁用控制
- plan 生成
- 构建前人工审批
- plan JSON 预览
- 本地产物列表和链接
- Agent / Skill 事件日志
- Harness 任务进度，包括当前阶段、已完成阶段数和 manifest 链接

Web UI 不替代 CLI。两者通过 service layer 复用同一套规划、Skill、QA 和构建逻辑，避免行为漂移。

## 多智能体 PPT 流水线

默认流程：

```text
DeckIntent
  -> supervisor_start node 创建请求上下文
  -> brief_outline node 生成 brief.json 和 outline.json
  -> brief_outline_eval node 评估 brief 和 outline
  -> content node 生成 content.json
  -> design_chart node 生成 design_chart.json
  -> content_eval node 评估 content
  -> design_chart_eval node 评估 design 和 chart 产物
  -> supervisor_merge node 合并为 slides_ir.json
  -> slides_ir_eval node 评估合并后的 IR
  -> qa node 生成 review_report.json
  -> supervisor_repair node 在需要时修订 slides_ir.json
  -> page_designer node 生成 page_design.json
  -> renderer_engineer node 查看渲染代码并生成 renderer_engineer_report.json
  -> page_generator node 将 slides_ir.json + page_design.json 转成 PptSpec
  -> render_review node 生成 render_review_report.json
  -> final_eval node 评估最终 QA 和渲染审查结果
  -> 现有 PPTX 渲染器生成 deck
  -> visual_quality_evaluator 审查生成后的 PPTX，并写入 *.visual_quality_report.json
```

默认 Agent：

| Agent | 职责 |
|---|---|
| Supervisor | 总指挥，负责决策、skill 分配、合并 Worker 产物、处理返修。 |
| Brief + Outline | 理解用户请求，生成需求 brief 和 15 页大纲。 |
| Content | 生成每页标题、核心信息、要点和讲稿备注。 |
| Design + Chart | 生成主题、版式规则、图表建议和视觉风格。 |
| QA | 检查 `slides_ir.json` 的结构、完整性和页面质量风险。 |
| Evaluator | 评估关键 Agent 产物，进行质量打分，并建议定向返工。 |
| Page Designer | 负责页面级设计决策，生成 `page_design.json`，包括版式、层级、密度和配图策略。 |
| Renderer Engineer | 查看 Page Generator / PPTX renderer 代码，判断 `page_design.json` 是否可实现，并写入 `renderer_engineer_report.json`，必要时生成任务级辅助脚本。 |
| Page Generator | 纯代码执行，将 `slides_ir.json` 和 `page_design.json` 转成 `PptSpec`，不使用 LLM。 |
| Render Review | 审查 Page Generator 映射结果，发现字段丢失、空白页等风险。 |

每次运行会写入任务目录：

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

默认页数是 `15` 页。每个 Agent 都是一个 LangGraph node。Worker 之间通过 JSON 文件协作，不直接修改彼此产物。Harness 归档会保存每个阶段的输入、输出、评估、状态、事件和恢复元数据，后续 UI 或 CLI 可以查看进度，并从已有中间产物继续。

Evaluator 作为旁路评估节点，会在关键产物生成后立即运行。系统先执行低成本规则检查；只有规则检查发现 warning/error 时，才调用 LLM Evaluator。触发条件包括 `severity = error`、`requires_rework = true` 或 `score < 0.75`。每个评估阶段最多触发一次局部返工。

## Harness 任务和失败恢复

Harness 任务命令用于查看和管理 `.ppt-agent/tasks/` 下归档的多智能体运行：

```bash
ppt-agent task list
ppt-agent task inspect <task-id-or-path>
ppt-agent task events <task-id-or-path> --limit 50
ppt-agent task artifacts <task-id-or-path>
ppt-agent task continue <task-id-or-path>
ppt-agent task continue <task-id-or-path> --auto-rework --max-rework 1
ppt-agent task approve <task-id-or-path> --stage plan_confirm --note "已确认"
ppt-agent task reject <task-id-or-path> --stage plan_confirm --reason "需要修改大纲"
ppt-agent task gates <task-id-or-path> --stage content
ppt-agent task preview <task-id-or-path>
ppt-agent task resume <task-id-or-path> --out resumed_plan.json
ppt-agent task retry-stage <task-id-or-path> content --reason "需要更新"
```

关键行为：

- `manifest.json` 记录任务状态、当前阶段、阶段输出、报告、最终 `ppt_spec` 和恢复提示。
- `logs/events.jsonl` 记录阶段级进度事件，可用于生成过程视图。
- 阶段目录保存 `input.json`、`output.json`、`eval.json` 和 `status.json` 快照。
- 质量门优先执行确定性检查，尽量在调用昂贵 LLM 评估前发现问题。
- 确认阶段（`plan_confirm`、`build_confirm`）可以暂停任务，等待用户批准或提出修改意见。
- 页面预览会基于归档的 `ppt_spec` 在 `previews/` 下写入轻量级逐页 JSON 和 HTML 预览。
- `retry-stage` 后接 `continue` 可以基于已归档的 `brief_outline` 产物，重新生成 `content` 及其下游阶段。Agent LLM 配置启用时会复用现有 LLM-capable pipeline node；LLM 不可用时回退到确定性逻辑。
- `--auto-rework` 可以开启有次数上限的质量门自动返工；默认仍然是人工审阅。
- `resume` 可以把归档中的最新 `ppt_spec` 导出为普通 plan 文件，继续构建或人工审阅。

Studio 也通过只读 API 暴露同样的数据：

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

## Agent 模型路由

每个 Agent 的模型路由配置文件：

```text
.ppt-agent/agents/config.json
```

默认模型分配：

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
| Page Generator | 无 | 无 |

查看或写入默认配置：

```bash
ppt-agent agent show-config
ppt-agent agent init-config
```

设置 DeepSeek API key：

```bash
ppt-agent llm set-key deepseek --api-key <your-key>
```

如果 Agent 模型调用失败，或者没有配置 API key，流水线默认会回退到确定性 Worker 逻辑。

## Skill 管理

导入的 skill 仍然放在现有 skill 目录中，尤其是：

```text
.ppt-agent/skills/
```

常用 skill 命令：

```bash
ppt-agent skill add <path-or-git-url>
ppt-agent skill list
ppt-agent skill validate <path-or-name>
ppt-agent skill init-template academic-paper-deck
ppt-agent skill init-template course-teaching-deck
ppt-agent skill init-template business-report-deck
ppt-agent skill init-template deck-quality-gate
```

也可以在规划或运行时直接应用模板：

```bash
ppt-agent plan "讲解 RAG 检索增强生成" --skill-template course-teaching-deck --spec plan.json
ppt-agent run "董事会 AI 采用汇报" --skill-template business-report-deck --out output/report.pptx
```

如果模板 Skill 尚不存在，PPT Agent 会自动创建到 `.ppt-agent/skills/`，并把 Skill 名写入 `DeckIntent.applied_skills`，让 Supervisor 可以分配给相关 Worker Agent。

多智能体 skill 管理原则：

```text
Supervisor 可以读取所有已启用 skill。
Supervisor 决定每个 Worker Agent 可以使用哪些 skill。
Worker Agent 只接收分配给自己的 skill context。
Page Designer 可以接收 Supervisor 分配的设计型、版式型 skill。
Renderer Engineer 可以接收 Supervisor 分配的渲染、代码、脚本类 skill。
Page Generator 不接收内容型或风格型 skill。
```

可以在 `skill.json` 中通过 `agent_scope` 指定 skill 适用范围：

```json
{
  "name": "executive-writing",
  "description": "面向高管汇报的 slide 写作规范。",
  "type": "markdown",
  "agent_scope": ["content", "qa"]
}
```

Skill manifest 也可以描述 v2 治理元数据：

```json
{
  "applies_to": ["paper", "course", "business"],
  "quality_gates": ["citation_required_when_evidence", "max_bullets_per_slide"],
  "artifacts": {"qa_rules": "qa_rules.json"},
  "examples": ["examples/request.json"],
  "version": "2.0.0"
}
```

也可以用下面的文件手动覆盖分配：

```text
.ppt-agent/agents/skills.json
```

示例：

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

每次运行时，最终的 `skill_policy`、`skill_catalog` 和 `skill_assignments` 会写入该任务的 `task_plan.json`。

## 反馈沉淀

可以用 feedback 命令沉淀已接受输出、项目偏好、纠错记录和失败模式，供后续规划或复盘使用：

```bash
ppt-agent feedback add "完整构建前优先做页面级预览" --type preference
ppt-agent feedback add "Content 阶段遗漏 citation" --type failure --task <task-id> --stage content
ppt-agent feedback accept <task-id> --note "已接受为销售汇报最终风格"
```

## 文档转 PPT

PPT Agent 支持从文档 evidence 生成可追踪的 PPTX。文档流程会把 Markdown 或 parser 输出转成 `evidence.json`，生成带 citation 的 schema-versioned `plan.json`，再构建 PPTX，并对 plan 执行确定性 QA / repair。

```bash
ppt-agent ingest input.md --out .ppt-agent/evidence.json
ppt-agent ingest paper.pdf --parser mineru --workdir .ppt-agent/parsed --out .ppt-agent/evidence.json
ppt-agent doctor
ppt-agent plan --evidence .ppt-agent/evidence.json --spec plan.json
ppt-agent build plan.json --evidence .ppt-agent/evidence.json --out deck.pptx
ppt-agent qa plan.json --evidence .ppt-agent/evidence.json --out qa_report.json
ppt-agent repair plan.json --qa qa_report.json --evidence .ppt-agent/evidence.json --out repaired_plan.json
```

中间文件：

- `evidence.json`：从 Markdown 或 parser 输出提取的结构化证据。
- `plan.json`：schema-versioned deck plan。
- `qa_report.json`：文档转 PPT 的确定性 QA 报告。

MinerU 是可选依赖。如果没有安装 MinerU，可以使用 Markdown 输入或预先生成的 parser 输出。

## LLM 配置

```bash
ppt-agent llm providers
ppt-agent llm configure --provider deepseek --model deepseek-v4-flash
ppt-agent llm set-key deepseek --api-key <your-key>
ppt-agent plan "季度产品路线图" --provider deepseek --model deepseek-v4-flash --spec plan.json
```

## 当前范围

默认 planner 对普通 deck 使用多智能体流水线。Evidence-backed planning 仍保留现有 evidence planner，以保持 citation 和 figure reference 的稳定性。Artifact 生成、schema 校验、plan migration、PPT build 和文件写入仍由代码控制。

## 记忆策略

Project memory 用于存储持久化项目偏好、已接受输出和失败模式。详见 [docs/memory-policy.md](docs/memory-policy.md)，其中说明了哪些内容可以记忆、哪些内容不能记忆，以及 workspace-scoped long-term memory 如何隔离和治理。
