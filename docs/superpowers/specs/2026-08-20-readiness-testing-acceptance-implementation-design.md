# 开发就绪、测试与场景验收：实现决策记录

## 文档状态

本文基于 [`2026-08-20-readiness-testing-acceptance-handoff.md`](2026-08-20-readiness-testing-acceptance-handoff.md)
的交接结论，在检查现有资产（`artifacts/skills/development-readiness/`、
`artifacts/workflows/ai-assisted-code-review/`、`artifacts/checklists/ai-assisted-code-review.md`）
后，对交接文档「十四、仍待决策」逐项给出第一版实现选择，并记录被否决的备选方案。

这些决策服务于第一版真实可用，不代表永久不变；发现真实使用问题后应回来更新本文而不是静默偏离。

## 一、开发前检查结论

对照交接文档第十一节逐项检查现有资产：

1. **Development Readiness Skill 已覆盖**：证据标签体系、三档状态（就绪/有限就绪/未就绪）、
   会话时间戳报告、有限自举授权与确认协议、环境验证子 Agent 契约、任务基准分支取证顺序。
   本次复用这些机制，不新建第二套。
2. **Development Readiness Skill 部分覆盖，需要补强**：
   - 未显式处理「接手者能力差异」（交接文档 3.4）。
   - 技术栈材料与项目/任务材料未明确拆分位置（交接文档 5.3）。
   - 原始资料（raw）没有固定归集位置（交接文档 5.4）。
   - 任务报告的「验收条件」字段存在，但未明确它是 Testing / Scenario Acceptance Skill 的
     标准输入之一。
   - 结束语只指向「新的 Coding Agent 会话」，未指向开发完成后的测试与验收阶段。
3. **不存在「既有成熟代码 Review Skill」**：仓库目前只有
   [`artifacts/workflows/ai-assisted-code-review/README.md`](../../../artifacts/workflows/ai-assisted-code-review/README.md)
   和配套 [`checklists/ai-assisted-code-review.md`](../../../artifacts/checklists/ai-assisted-code-review.md)，
   是一次性提示词 + 人工检查清单，不是常驻 Skill，也没有固定的落盘产物格式。交接文档 11.6
   假设的「消费既有代码 Review Skill 报告」在本仓库当前不成立。Scenario Acceptance Skill
   必须能在代码 Review 证据以「PR 评论」「Checklist 勾选结果」「口头小结」等任意形式存在、
   或完全缺失时都不崩溃，把缺失当作证据缺口按风险分级处理，而不是假设总有结构化报告可读。
4. **不存在浏览器测试或视觉测试专用子 Skill**：development-readiness 的 `methodology.md` 已有
   UI 页面验证等级（L1–L4）的雏形，可作为 Testing Skill 浏览器功能测试小节的起点，避免另创一套
   等级定义。

## 二、共享的最小输入输出协议

三个 Skill 共享同一条材料链，第一版不引入 manifest 或索引文件，改用「同名任务 slug + 各 Skill
自己的目录」让材料可以相互定位：

```text
docs/development-readiness/<ts>-baseline.md
docs/development-readiness/tasks/<ts>-<task>.md
docs/development-readiness/tasks/raw/<ts>-<task>/...
docs/testing/tasks/<ts>-<task>.md
docs/scenario-acceptance/tasks/<ts>-<task>.md
```

- `<task>` 是开发就绪阶段确定的任务 slug，后续 Testing、Scenario Acceptance 报告复用同一
  slug，但各自使用自己会话的时间戳；报告开头必须写回链的具体路径，不假设读者已知。
- 任一 Skill 发现引用的上游文件缺失、slug 冲突或明显对应错代码版本，必须先停下确认，不能
  假装继续。
- 都放在实际相关项目目录下（例如 monorepo 中的子项目），不是工作区根目录；只有用户明确要求
  工作区级输出，或任务确实跨项目时，才在工作区根目录新增协调文件（见「五」）。

## 三、逐项决策

### 3.1 项目内文档根目录和最终文件命名

- 沿用已经跑通的 `docs/development-readiness/`。
- 新增 `docs/testing/tasks/<ts>-<task>.md`、`docs/scenario-acceptance/tasks/<ts>-<task>.md`。
- 不新建 `baseline` 概念给 Testing / Scenario Acceptance：两者天然是任务级、一次性证据/结论，
  没有需要跨任务复用的「环境基线」；需要复用的项目全局事实统一去读
  `docs/development-readiness/<ts>-baseline.md`。
- 否决方案：把三个 Skill 的产物合并进同一个目录（如 `docs/handoff/`）。否决原因：会混淆各
  Skill 的所有权与更新节奏，且违反「新版本完整、历史版本可追溯」的既有约定。

### 3.2 项目 baseline、技术栈文档与任务材料的拆分

- 默认技术栈事实仍写在 `report-template-base.md` 的「项目开发必需信息」小节，第一版**不**强制
  拆分独立文件。
- 只有当同一项目的技术栈说明明显膨胀（需要多个子技术栈、多运行时、多个入口应用）时，才允许
  拆出 `docs/development-readiness/<ts>-tech-stack.md` 并在基线中引用；这是允许项，不是默认
  流程，避免过早抽象。
- 任务级文档只保留「本任务用到技术栈的哪一部分、可参考哪些实现、接手者在哪里可能不熟悉」，
  不复制项目级技术栈全文。

### 3.3 任务状态、索引与历史版本

- 不引入 manifest、状态机或索引文件。任务是否存在、处于哪个阶段，由三个目录下是否存在对应
  `<ts>-<task>.md` 及其内部「结论」小节直接表达：
  - 只有 `development-readiness/tasks/*` → 尚未开发或开发中。
  - 追加 `testing/tasks/*` → 已完成实现并进入测试。
  - 追加 `scenario-acceptance/tasks/*` → 已进入验收。
- 同一任务多轮测试/验收时，同一会话内更新同一份文件；新会话（新一轮测试或验收）创建新时间戳
  版本，旧版本保留用于追溯，不覆盖。
- 否决方案：为每个任务维护一个跨 Skill 的状态 JSON。否决原因：交接文档 3.3 明确要求第一版不
  引入这类机制，目录 + 文件本身已经够表达。

### 3.4 外部资料快照的授权、安全与更新策略

- 新增约定：任务相关的原始资料（需求文档快照、听记、附件）归集到
  `docs/development-readiness/tasks/raw/<ts>-<task>/`，任务报告的「任务与来源」字段记录该目录
  路径和采集时间。
- 处理方式按交接文档 5.4 的四类来源分别处理（用户提供快照 / 仓库权威文件引用原路径 / 外部
  可变来源留存快照 + 原链接 + 采集时间 / 敏感材料只记安全引用与必要摘要)。
- 快照默认是**只读留存**，不代表已核实为最新事实；任务报告仍需为其中的结论标注证据等级。

### 3.5 Testing Skill 的形态：单一 Skill + 按需 reference，还是拆出浏览器测试子 Skill

- 采用**单一 Testing Skill**，浏览器功能测试与视觉测试作为按需加载的 `reference/`
  （`browser-functional-testing.md`、`visual-testing.md`），不拆成独立 Skill。
- 原因：三者共享同一份验收标准输入、同一份报告和同一套授权模型，拆分会导致按需浏览器测试
  和主测试流程反复交接上下文；这与 development-readiness 用 reference 承载 UI 页面验证等级的
  既有模式一致。

### 3.6 测试报告与代码 Review 报告的标准接口

- 由于代码 Review 目前只是工作流 + 检查清单，不强制它输出固定 Schema。Scenario Acceptance
  Skill 只要求代码 Review 证据具备三类最小信息：**结论来源**（谁/什么方式审查）、**是否存在
  阻塞项及内容**、**审查所基于的代码版本**。缺一类就在验收报告中记为证据缺口，按任务风险决定
  是否阻断验收，而不是直接判定「无法验收」。
- 为提升未来兼容性，给 `artifacts/checklists/ai-assisted-code-review.md` 和配套 README 补充
  一句「结论如何供场景验收消费」的说明，不改变其作为人工检查清单的性质。

### 3.7 场景验收对「有条件通过」的授权与记录方式

- 报告中的「有条件通过」必须同时记录：条件内容、接受者（角色名，不编造人名）、接受时间、
  是否有后续任务或 Issue 跟踪剩余风险。缺少接受者或条件内容时不能写「有条件通过」，只能写
  「需要业务或用户决策」。

### 3.8 面向非技术参与者的交互层与技术证据层分层

- 三个 Skill 的报告统一延续 development-readiness 已经验证过的「先结论、后证据」结构：
  第 1 部分是非技术也能读懂的结论、风险、下一步；技术证据、命令、日志位置放在后续小节。
- 不新增单独的「非技术摘要文件」，避免制造第二个需要同步的产物；分层通过章节顺序表达即可。

## 四、建议开发顺序的落地方式

对照交接文档第十二节，第一版实际交付：

1. 复用 development-readiness 现有 baseline / 任务样本作为「陌生项目接手」参考，不新建
   评估用测试仓库（后续用真实任务验证时再补场景样本）。
2. 落地本文第二节的最小输入输出协议。
3. 修订 development-readiness：能力适配、raw 归集、下游交接指向、多任务状态说明。
4. 落地 Testing Skill 主流程（非浏览器测试为主）与标准报告。
5. Testing Skill 增加按需浏览器功能测试、条件化视觉测试 reference。
6. 落地 Scenario Acceptance Skill，消费 Testing 报告与（可能存在或缺失的）代码 Review 证据。
7. 用 `artifacts/evaluations/` 新增的场景化评估文档验证端到端可追溯性。
8. 是否需要更复杂的时效性或版本机制，留给真实失败案例出现后再决定（第一版不做）。

## 五、多项目协调文件（按需）

只有当一个任务确实跨越多个项目时，才在工作区根目录新增
`docs/workspace-coordination/<ts>-<task>.md`，记录共同目标、涉及项目列表及其各自材料路径、
项目间依赖与整体状态；三个 Skill 都不得用它替代各项目内的材料。多数单项目任务不需要这个文件。
