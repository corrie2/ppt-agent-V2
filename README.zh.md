# PPT Agent

PPT Agent 是一个面向 PowerPoint 自动生成的多智能体 CLI 运行时。它通过结构化 JSON 中间产物组织 PPT 制作流程，由多个专业 Agent 分工完成需求解析、大纲、文案、设计、质检和渲染审查，最终由确定性代码生成 PPT 文件。

默认的普通 `plan` / `run` 路径已经接入 Supervisor / Worker 多智能体流水线。Worker Agent 只生成结构化产物，Supervisor Agent 负责决策、合并、分配 skill 和处理返修，PowerPoint 文件写入只由受控运行时代码完成。

English documentation: [README.md](README.md)

## 架构

- `runtime/multi_agent_pipeline.py`：基于 LangGraph 的多智能体 PPT 规划流水线，每个 Agent 都对应一个 graph node。
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
```

对于非 evidence 驱动的普通 PPT，默认 `plan` / `run` 会使用多智能体流水线。

## 多智能体 PPT 流水线

默认流程：

```text
DeckIntent
  -> supervisor_start node 创建请求上下文
  -> brief_outline node 生成 brief.json 和 outline.json
  -> brief_outline_eval node 评估 brief 和 outline
  -> content node 生成 content.json
     || design_chart node 生成 design_chart.json
  -> join 等待 content.json 和 design_chart.json
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

默认页数是 `15` 页。每个 Agent 都是一个 LangGraph node。Worker 之间通过 JSON 文件协作，不直接修改彼此产物。

Evaluator 作为旁路评估节点，会在关键产物生成后立即运行。系统先执行低成本规则检查；只有规则检查发现 warning/error 时，才调用 LLM Evaluator。触发条件包括 `severity = error`、`requires_rework = true` 或 `score < 0.75`。每个评估阶段最多触发一次局部返工。

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
```

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
