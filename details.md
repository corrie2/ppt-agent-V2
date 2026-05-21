# PPT Agent 多智能体流程细节

本文档说明当前 PPT Agent 的多智能体执行流程。当前设计目标是：

```text
外层保持原有 CLI / LangGraph 入口兼容
内层 PPT 生产由多 Agent LangGraph 子图完成
每个 Agent 都是 LangGraph node
各 Agent 通过结构化 JSON artifact 协作
Supervisor 负责总调度、合并、skill 分配和局部返工
Evaluator 独立于生产流程之外，只旁路评估，不直接修改产物
Page Generator 只做确定性转换，不使用 LLM
Renderer Engineer 可以查看 Page Generator / PPTX renderer 代码，使用 deepseek-v4-pro
```

## 0. 当前多智能体协作框架速览

当前框架可以理解为“外层统一入口 + 内层多智能体生产子图 + artifact 协作协议”。

### 0.1 两个入口，共用一套生产能力

```text
CLI / Shell / Web UI
  |
  v
ShellSession + SkillRegistry
  |
  v
planner / skill / build service
  |
  v
multi-agent pipeline
```

- CLI 入口包括 `ppt-agent plan`、`ppt-agent run`、`ppt-agent build` 和交互式 `ppt`。
- Web 入口是 `ppt-agent serve` 启动的 PPT Agent Studio。
- CLI 和 Web 不应该各自实现一套逻辑；它们都应复用 `ShellSession`、`SkillRegistry`、planner、QA 和 build skills。
- Web 端只是把 workspace、draft request、plan、pending build、artifacts 和 skill 状态可视化。

### 0.2 多智能体不是多个进程，而是 LangGraph 节点协作

当前的多智能体协作框架主要在：

```text
src/ppt_agent/runtime/multi_agent_pipeline.py
```

每个 Agent 是 LangGraph `StateGraph` 里的一个 node。它们不是长期运行的独立进程，而是在一次规划任务中按图执行、读取上游 artifact、写入自己负责的 artifact。

核心原则：

```text
Agent 不直接互相改内存
Agent 不直接写 PPTX
Agent 通过 JSON artifact 交换结果
Supervisor 负责合并和返工决策
Evaluator 只评估，不直接改产物
Page Generator 确定性生成 PptSpec，不调用 LLM
```

### 0.3 协作数据流

```text
DeckIntent
  -> user_request.json
  -> brief.json + outline.json
  -> content.json
  -> design_chart.json
  -> slides_ir.json
  -> review_report.json
  -> page_design.json
  -> renderer_engineer_report.json
  -> PptSpec
  -> render_review_report.json
  -> final evaluation
  -> build_pptx()
  -> visual_quality_report.json
```

`slides_ir.json` 是关键合并点。它之前是需求、内容、设计的分工生产；它之后是页面设计、渲染可行性检查、确定性 page generation 和渲染审查。

### 0.4 角色分工

| 角色 | 作用 | 是否直接生产 PPTX |
|---|---|---:|
| Supervisor | 创建任务上下文、分配 skill、合并 worker 结果、决定是否返工 | 否 |
| Brief + Outline | 理解需求，产出 brief 和页面大纲 | 否 |
| Content | 产出每页标题、核心信息、bullets、speaker notes | 否 |
| Design + Chart | 产出主题、版式方向、图表建议、视觉策略 | 否 |
| QA | 检查 `slides_ir.json` 的结构、完整性、密度风险 | 否 |
| Evaluator | 对关键阶段做质量门禁，建议是否局部返工 | 否 |
| Page Designer | 基于 `slides_ir.json` 做页面级布局和视觉层级决策 | 否 |
| Renderer Engineer | 检查 renderer 能否落地 `page_design.json`，必要时给出脚本/实现建议 | 否 |
| Page Generator | 把 `slides_ir.json + page_design.json` 确定性转换成 `PptSpec` | 否 |
| Render Review | 检查 Page Generator 映射是否丢字段或可能空白页 | 否 |
| Visual Quality Evaluator | PPTX 生成后做视觉质量评估 | 否 |
| PPTX runtime | 唯一真正写 PPTX 文件的确定性代码 | 是 |

### 0.5 返工机制

返工不是整条流水线重跑，而是局部打回。

```text
worker artifact
  -> evaluator
  -> pass: 进入下一阶段
  -> fail: 最多局部返工一次
```

触发返工的典型条件：

- `severity = error`
- `requires_rework = true`
- `score < 0.75`
- QA 发现阻断问题，例如缺标题、缺核心 message、页码不连续、layout 缺失

Evaluator 不直接修改 artifact。它只写 evaluation report；真正的修复由对应 Worker 或 Supervisor 决定。

### 0.6 Skill 协作方式

skill 不是启动时让用户手动选择的开关，而是能力库。

```text
load_user_skills()
  -> Supervisor skill catalog
  -> assign_skills_to_agents()
  -> each worker receives assigned skill context
```

规则：

- Supervisor 可以读取所有可用 skill。
- Worker 只能收到分配给自己的 skill context。
- `agent_scope` 可以显式限制 skill 给哪些 Agent。
- `.ppt-agent/agents/skills.json` 可以手动覆盖分配。
- Page Generator 不使用内容/风格 skill，保持确定性转换。

### 0.7 模型路由

模型路由配置在：

```text
.ppt-agent/agents/config.json
```

默认策略：

- 决策和工程难度更高的角色使用 `deepseek-v4-pro`，例如 Supervisor、Renderer Engineer、Visual Quality Evaluator。
- 内容和设计类 worker 默认使用 `deepseek-v4-flash`。
- Page Generator 不使用 LLM。
- 如果某个 Agent 的模型调用失败，且允许 fallback，则回退到确定性逻辑。

### 0.8 当前框架边界

当前多智能体框架负责“规划和结构化生产”，不负责直接操作 PowerPoint 文件。

边界如下：

```text
multi-agent pipeline: 生成结构化 PptSpec
runtime/pptx.py: 根据 PptSpec 写 PPTX
runtime/html_deck.py: 根据 plan/spec 写 HTML deck
visual_quality.py: 对生成后的 PPTX 做质量评估
server/app.py: 提供 Web UI 和 REST API
app/web_service.py: 让 Web 复用 shell/session/skill 能力
```

这套边界的好处是：Agent 可以灵活协作和返工，但最终文件写入仍由可控代码完成，减少 LLM 直接写文件带来的不可预测性。

## 1. 总体架构

```text
用户命令
  |
  v
CLI: ppt-agent plan / run
  |
  v
外层 LangGraph / CLI 入口
  |
  v
runtime.planner.build_plan_spec()
  |
  v
多智能体 LangGraph 子图 create_multi_agent_graph()
  |
  v
PptSpec
  |
  v
build_pptx()
  |
  v
PPTX 文件
  |
  v
visual_quality_evaluator
  |
  v
*.visual_quality_report.json
```

说明：

- 普通非 evidence deck 默认走多智能体流水线。
- evidence-backed deck 仍保留 evidence planner，以保证 citation 和 figure reference 稳定。
- 所有 Worker 只生成结构化中间产物，不直接写 PPTX。
- PowerPoint 文件写入仍由受控 runtime 代码完成。
- PPTX 生成后由 Visual Quality Evaluator 进行最终视觉质量评估。

## 2. 当前主流程图

```text
┌──────────────────────────────┐
│ supervisor_start              │
│ 角色: Supervisor              │
│ 输出: user_request.json       │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ brief_outline                 │
│ 角色: Worker                  │
│ 输出: brief.json / outline    │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ brief_outline_eval            │
│ 角色: Evaluator               │
│ 不通过: 打回 brief_outline    │
└───────────────┬──────────────┘
        ┌───────┴────────┐
        v                v
┌─────────────────┐ ┌──────────────────────────────┐
│ content         │ │ design_chart                  │
│ 角色: Worker    │ │ 角色: Worker                  │
│ 输出: content   │ │ 输出: design_chart.json       │
└────────┬────────┘ └───────────────┬──────────────┘
         └──────────────┬───────────┘
                        v
┌──────────────────────────────┐
│ join                         │
│ 等待 content + design_chart   │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ content_eval                  │
│ 角色: Evaluator               │
│ 不通过: 打回 content          │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ design_chart_eval             │
│ 角色: Evaluator               │
│ 不通过: 打回 design_chart     │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ supervisor_merge              │
│ 角色: Supervisor              │
│ 输出: slides_ir.json          │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ slides_ir_eval                │
│ 角色: Evaluator               │
│ 不通过: 打回 supervisor_merge │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ qa                            │
│ 角色: Reviewer                │
│ 输出: review_report.json      │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ QA gate                       │
│ 有阻断问题 -> supervisor_repair│
│ 无阻断问题 -> page_designer    │
└───────┬──────────────────────┘
        │
        ├──────────────┐
        v              v
┌──────────────────┐  ┌──────────────────────────────┐
│ supervisor_repair│  │ page_designer                 │
│ 角色: Supervisor │  │ 角色: Worker                  │
│ 修订 slides_ir   │  │ 输出: page_design.json        │
└────────┬─────────┘  └───────────────┬──────────────┘
         └────────────────────────────┘
                         v
┌──────────────────────────────┐
│ renderer_engineer             │
│ 角色: Worker / Engineer       │
│ 模型: deepseek-v4-pro         │
│ 输出: renderer_engineer_report│
│ 可输出: renderer_scripts/     │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ page_generator                │
│ 角色: Executor                │
│ 输入: slides_ir + page_design │
│ 输出: PptSpec                 │
│ 不使用 LLM                    │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ render_review                 │
│ 角色: Reviewer                │
│ 输出: render_review_report    │
└───────────────┬──────────────┘
                v
┌──────────────────────────────┐
│ final_eval                    │
│ 角色: Evaluator               │
│ 通过 -> END                   │
│ 不通过 -> 局部打回目标 Agent  │
└──────────────────────────────┘
```

最终评估不通过时，只会局部打回 `renderer_engineer`、`page_generator` 或 `render_review`，不会整轮重做。

## 3. Agent 角色分类

| 类型 | Agent | 默认模型 | 是否生产内容 | 产物/权限 |
|---|---|---|---:|---|
| Supervisor | `supervisor_start` / `supervisor_merge` / `supervisor_repair` | `deepseek-v4-pro` | 是 | 决策、合并、修订 |
| Worker | `brief_outline` | `deepseek-v4-flash` | 是 | `brief.json` / `outline.json` |
| Worker | `content` | `deepseek-v4-flash` | 是 | `content.json` |
| Worker | `design_chart` | `deepseek-v4-flash` | 是 | `design_chart.json` |
| Worker | `page_designer` | `deepseek-v4-flash` | 是 | `page_design.json` |
| Worker / Engineer | `renderer_engineer` | `deepseek-v4-pro` | 是 | `renderer_engineer_report.json` / `renderer_scripts/` |
| Evaluator | `visual_quality_evaluator` | `deepseek-v4-pro` | 否 | `*.visual_quality_report.json` |
| Reviewer | `qa` | `deepseek-v4-flash` | 否 | `review_report.json` |
| Reviewer | `render_review` | `deepseek-v4-flash` | 否 | `render_review_report.json` |
| Evaluator | `brief_outline_eval` / `content_eval` / `design_chart_eval` / `slides_ir_eval` / `final_eval` | `deepseek-v4-flash` | 否 | 只评估，不修改产物 |
| Executor | `page_generator` | 无 LLM | 否 | 生成 `PptSpec` |

角色区别：

- **Supervisor**：总指挥，负责决策、合并、skill 分配和局部返工。
- **Worker**：生产某一类中间产物。
- **Renderer Engineer**：属于工程型 Worker，可以看 renderer 代码，判断设计是否能落地，并生成任务级辅助脚本。
- **Visual Quality Evaluator**：PPTX 生成后的视觉质量评估 Agent，评估页面密度、图片落地、空白页风险和研究生汇报观感。
- **Reviewer**：生产流程内部质检，检查具体产物问题。
- **Evaluator**：旁路评估 Agent，评估 Agent 工作质量和是否需要局部返工。
- **Executor**：确定性执行转换，不做内容决策，不使用 LLM。

## 4. 节点细节

### 4.1 `supervisor_start`

输入：`DeckIntent`

输出：

```text
user_request.json
```

职责：

- 接收用户请求。
- 初始化任务上下文。
- 记录默认页数、受众、语言、输出目标等信息。

### 4.2 `brief_outline`

输入：

```text
user_request.json
assigned skills
```

输出：

```text
brief.json
outline.json
```

职责：

- 理解主题、受众、语气和页数。
- 默认生成 15 页大纲。
- 只负责需求理解和结构，不写完整页面文案。

### 4.3 `content`

输入：

```text
brief.json
outline.json
assigned skills
```

输出：

```text
content.json
```

职责：

- 生成每页标题、核心 message、bullets、speaker notes。
- 控制每页可见 bullet 数量，避免页面过密。

### 4.4 `design_chart`

输入：

```text
brief.json
outline.json
assigned skills
```

输出：

```text
design_chart.json
```

职责：

- 生成主题、配色、字体、版式规则。
- 生成图表建议。
- 不写正文内容。

### 4.5 `supervisor_merge`

输入：

```text
brief.json
outline.json
content.json
design_chart.json
```

输出：

```text
slides_ir.json
```

职责：

- 合并所有 Worker 产物。
- 解决字段冲突。
- 生成统一页面中间表示 `slides_ir`。

### 4.6 `qa`

输入：

```text
slides_ir.json
```

输出：

```text
review_report.json
```

职责：

- 检查 `slides_ir` 是否存在阻断问题。
- 阻断问题包括缺 title、缺 message、页码不连续、layout 缺失等。
- 如果有阻断问题，流程进入 `supervisor_repair`。
- 如果没有阻断问题，流程进入 `page_designer`。

### 4.7 `supervisor_repair`

输入：

```text
slides_ir.json
review_report.json
```

输出：

```text
修订后的 slides_ir.json
```

职责：

- 只修复 QA 指出的阻断问题。
- 不重新生成整轮内容。
- 修复后继续进入 `page_designer`。

### 4.8 `page_designer`

模型：`deepseek-v4-flash`

输入：

```text
slides_ir.json
review_report.json
assigned design/layout skills
```

输出：

```text
page_design.json
```

职责：

- 做页面级设计决策。
- 为每页选择最终 layout。
- 标注视觉优先级、信息层级、密度、配图策略和 renderer notes。
- 不改 title、message、bullets。
- 不生成 PPTX。

### 4.9 `renderer_engineer`

模型：`deepseek-v4-pro`

输入：

```text
slides_ir.json
page_design.json
Page Generator / PPTX renderer 代码上下文
assigned renderer/code/script skills
```

输出：

```text
renderer_engineer_report.json
renderer_scripts/
```

职责：

- 查看 Page Generator / PPTX renderer 的关键代码上下文。
- 判断 `page_design.json` 中的 layout、图片策略、密度要求是否能被当前 renderer 实现。
- 如果 renderer 能力不足，输出 `extension_plan`。
- 必要时生成任务级辅助脚本到 `renderer_scripts/`。
- 不直接修改项目源码。
- 不修改 `slides_ir.json`、`page_design.json`、`PptSpec` 或 PPTX 文件。

### 4.10 `page_generator`

模型：无 LLM

输入：

```text
slides_ir.json
page_design.json
```

输出：

```text
PptSpec
```

职责：

- 只做确定性转换。
- 将 `slides_ir + page_design` 转成 `PptSpec`。
- 不读取 Worker 原始文件。
- 不使用 skill。
- 不调用 LLM。

### 4.11 `render_review`

模型：`deepseek-v4-flash`

输入：

```text
slides_ir.json
page_design.json
renderer_engineer_report.json
PptSpec summary
```

输出：

```text
render_review_report.json
```

职责：

- 检查 `slides_ir + page_design -> PptSpec` 映射是否完整。
- 检查是否丢 title、message、bullets、layout。
- 检查 Page Designer 的 layout 决策是否被 Page Generator 应用。
- 检查空白页风险和 renderer 映射风险。
- 只报告问题，不修改产物。

### 4.12 `final_eval`

模型：`deepseek-v4-flash`

输入：

```text
review_report.json
renderer_engineer_report.json
render_review_report.json
PptSpec summary
```

输出：

```text
evaluation_report.json
```

职责：

- 做最终旁路评估。
- 如果发现阻断问题，只局部打回目标 Agent。
- 可打回目标：`renderer_engineer`、`page_generator`、`render_review`。
- 每个评估阶段最多触发 1 次局部返工。

### 4.13 `visual_quality_evaluator`

模型：`deepseek-v4-pro`

执行位置：`build_pptx()` 之后、外层 QA 之前。

输入：

```text
PPTX 文件
PptSpec
page_design.json
renderer_engineer_report.json
render_review_report.json
PPTX 结构化视觉指标
```

输出：

```text
*.visual_quality_report.json
```

职责：

- 从 PPTX 提取结构化视觉指标，包括每页 shape 数、文本量、图片数、表格数、规划 layout、规划 figure ids。
- 使用 `deepseek-v4-pro` 评估最终 PPT 视觉质量。
- 判断页面是否过密、是否疑似空白、是否遗漏计划中的图片、是否适合研究生汇报。
- 给出每页评分、总分、问题列表和建议打回目标。
- 不直接修改 PPTX，不直接修改任何中间产物。

说明：

```text
当前版本先使用 PPTX 结构化指标进行评估。
后续可以继续增强为 PPTX -> slide screenshot -> 多模态视觉评估。
```

## 5. Evaluator 策略

Evaluator 独立于生产流程之外，不直接修改任何主产物。

当前策略：

```text
先做规则检查
  |
  |-- 没有 warning/error
  |     -> 不调用 LLM Evaluator
  |
  |-- 有 warning/error
        -> 调用 deepseek-v4-flash Evaluator
        -> 只输出 evaluation_report.json
```

触发局部返工的条件：

```text
severity = error
requires_rework = true
score < 0.75
```

返工限制：

```text
每个评估阶段最多返工 1 次
只打回对应 Agent
不整轮重做
Evaluator 不直接修改任何产物
```

打回映射：

| 评估节点 | 不合格时打回 |
|---|---|
| `brief_outline_eval` | `brief_outline` |
| `content_eval` | `content` |
| `design_chart_eval` | `design_chart` |
| `slides_ir_eval` | `supervisor_merge` |
| `final_eval` | `renderer_engineer`、`page_generator` 或 `render_review` |

## 6. Artifact 目录

每轮任务会写入：

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

## 7. Artifact Ownership

| 文件 | Owner |
|---|---|
| `user_request.json` | Supervisor |
| `task_plan.json` | Supervisor |
| `brief.json` | Brief + Outline Worker |
| `outline.json` | Brief + Outline Worker |
| `content.json` | Content Worker |
| `design_chart.json` | Design + Chart Worker |
| `slides_ir.json` | Supervisor |
| `review_report.json` | QA Reviewer |
| `evaluation_report.json` | Evaluator |
| `page_design.json` | Page Designer Worker |
| `renderer_engineer_report.json` | Renderer Engineer Worker |
| `renderer_scripts/` | Renderer Engineer Worker |
| `render_review_report.json` | Render Review Reviewer |
| `PptSpec` | Page Generator Executor |

原则：

```text
Worker 只写自己的 artifact
Reviewer 只写 review report
Evaluator 只写 evaluation report
Page Designer 只写 page_design.json
Renderer Engineer 只写 renderer_engineer_report.json 和任务级 renderer_scripts/
Executor 只做确定性转换
Supervisor 负责合并和修订
```

## 8. Skill 管理流程

```text
导入 skill
  |
  v
.ppt-agent/skills/
  |
  v
Supervisor 读取所有已启用 skill
  |
  v
Supervisor 根据 agent_scope 或规则分配 skill
  |
  v
Worker 只收到自己的 assigned_skills
```

规则：

```text
Supervisor 可以读取所有 skill
Worker 只能使用分配给自己的 skill
Page Designer 可以使用被分配的设计型、版式型 skill
Renderer Engineer 可以使用被分配的渲染、代码、脚本类 skill，并可以查看 renderer 代码上下文
Page Generator 不接收 skill
Evaluator 可以看到 skill 分配结果，用于审计是否越权
```

手动分配文件：

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

## 9. 模型路由

默认模型：

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

配置文件：

```text
.ppt-agent/agents/config.json
```

如果某个 Agent 的模型调用失败，且允许 deterministic fallback，则该节点会回退到规则逻辑。

## 10. 当前核心流程总结

```text
Supervisor 负责总调度
Brief/Content/Design Worker 分别生产结构化中间产物
Evaluator 在关键阶段做旁路即时评估
QA 做生产流程内审查
Page Designer 决定页面长什么样
Renderer Engineer 判断 renderer 能不能做出来，并按需生成任务级辅助脚本
Page Generator 只负责确定性 PptSpec 转换
Render Review 检查映射和渲染风险
Final Eval 决定是否局部返工
Visual Quality Evaluator 在 PPTX 生成后检查最终视觉质量
```
