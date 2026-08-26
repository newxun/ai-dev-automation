---
name: yunxiao-devops-workflows
description: Use whenever 对话中出现云效（阿里云 Yunxiao / alibabacloud-devops-mcp-server）MCP 工具调用，或用户提到"云效"/"yunxiao"/相关需求、任务、流水线、发布单、代码仓库、工时——不限于开发前同步/流水线排查/拆任务与工时这三个内置场景，只要在用云效 MCP 就应加载，以复用身份/上下文缓存与写操作安全模型。
disable-model-invocation: true
---

# Yunxiao DevOps Workflows

一层「云效 MCP 使用规范」，不新增能力，只把云效 MCP Server 暴露的 194 个原始工具（8 个 toolset：组织管理、代码管理 Codeup、项目协作 Projex、流水线 Flow、制品仓库 Packages、应用交付 AppStack、测试管理 Testhub）统一到一套身份/上下文缓存和写操作安全模型之下，并在此基础上沉淀了三条高频可复用工作流。运行于任意接入了云效 MCP 的 Coding Agent（Cursor、Claude Code、Codex 等）。

你**不是**新的 API 封装，云效 MCP 已经支持自然语言直接调用；本 Skill 解决的是「多步骤取证顺序、ID/字段/工作流约定、写操作前该确认到什么颗粒度」，避免每次都重新摸索或误触发生产副作用。

## 前置检查

对话中若没有出现云效相关工具，先确认用户已按官方文档接入 MCP（远程托管 `https://openapi-rdc.aliyuncs.com/ai/mcp` 或 stdio `alibabacloud-devops-mcp-server`，Region 版替换为组织域名 `/ai/mcp`），并建议按将要执行的工作流只挂对应 `toolsets`，减少上下文占用：

- 只做「开发前同步」→ `project-management,code-management`
- 只做「流水线/发布排查」→ `pipeline-management,application-delivery,code-management`
- 只做「拆任务与工时」→ `project-management`

## 身份与上下文解析（任何云效 MCP 调用都适用，不只三条工作流）

1. **先查本地缓存**：读 `memory/context.json`（不存在就参照 `memory/context.example.json` 的结构）。缓存里有组织 ID、当前用户 ID、常用项目 ID、字段约定（如"参与者"具体指哪个字段）时直接复用，不重新调用身份类工具——这是同一份缓存跨对话、跨会话复用的地方，比每次现查或指望上层 Agent 记住更可靠。
2. 缓存不存在、缓存字段为空、或按缓存的 ID 实际调用失败（如 404/403，说明组织/项目已变化）时，才用只读工具现查：`get_current_organization_info` / `get_user_organizations` 确认组织 → `search_projects` / `get_project` 确认项目空间 → 视需要 `get_current_user` 拿真实用户 ID。查到后写回（或新建）`memory/context.json` 并刷新 `updatedAt`；这是本地文件读写，不涉及任何外部系统，不需要额外找用户确认。
3. 用户在本轮对话里明确给出的组织/项目/工作项/流水线/仓库 ID 或链接，优先级高于缓存，直接用，不用缓存里的旧值覆盖。
4. 涉及"我的 / 分配给我的"这类查询时，优先用 `search_workitems` 等工具原生支持的 `assignedTo="self"` / `creator="self"` 简写，不需要解析真实用户 ID；只有按原生 `participants` 参与人字段过滤（必须走 `advancedConditions` 传真实 ID）或简报里要展示用户姓名时，才需要用到缓存（或现查）的 `currentUserId`。

## 安全模型（三条工作流共用，贯穿始终）

- **只读工具**：随时自由调用（`get_*` / `list_*` / `search_*`），这是取证的主要手段，不需要每次确认。
- **协作类写操作**（工作项评论、合并请求评论、提交评论、工时登记/更新，如 `create_work_item_comment`、`create_change_request_comment`、`create_commit_comment`、`create_effort_record`、`update_effort_record`、`create_estimated_effort`）：先把要发送的文案或要登记的数值原样展示给用户确认，再调用——这些内容对团队可见，写错会误导协作方或污染工时统计。
- **结构类写操作**（`create_work_item`、`update_work_item`、`create_branch` / `delete_branch`、`create_sprint` / `update_sprint`、`create_version` / `update_version` / `delete_version`、`create_testcase` / `delete_testcase`、`update_test_result` 等）：先说明影响范围（会通知谁、会改变哪个状态机/迭代/版本），确认后再执行。
- **高风险写操作**（触发外部系统副作用或生产影响，如 `create_pipeline_run`、`execute_pipeline_job_run`、`update_pipeline`、`create_pipeline_from_description`、`stop/skip/retry/resume_vm_deploy_*`、`execute_job_action`、`create_change_order`、`create/cancel/close_appstack_change_request`、`execute_app_release_stage` 及各类 `*_release_stage_*`）：**必须**先给出「目标环境 + 影响范围（分支/版本/是否生产）+ 失败或误触发后的回滚方式」，拿到用户明确同意后才调用。不能因为用户话里提到"部署""发布"就默认已经获得连续触发授权；一次授权只覆盖当前这一次动作。

## 三条内置工作流（可选，按意图触发）

上面的身份解析与安全模型适用于**任何**云效 MCP 调用；下面三条是目前沉淀出的高频场景，命中时按用户意图选择对应 reference 读入，不要三份一起读，也不要因为意图不属于这三条就跳过身份解析和安全模型：

1. **开发前同步需求/任务/评论** → `reference/dev-context-sync.md`：按「我参与的所有需求 / 编号 / 关键字」三种方式定位需求，拉取挂在需求下责任人为我的任务，汇总需求层和任务层的全部评论、字段、活动历史、关联合并请求，转成可执行的任务简报；可作为 [`development-readiness`](../development-readiness/SKILL.md) 「任务就绪」阶段的信息来源之一，而不是另起一套任务边界判断。
2. **发起流水线 / 排查失败日志 / 处理发布冲突** → `reference/pipeline-release.md`：定位流水线与运行记录、拉取失败 Job 日志、区分失败原因（编译测试/环境/审批/代码冲突），高风险动作前强制走确认清单。
3. **需求拆任务、登记与统计工时** → `reference/task-timesheet.md`：确认工作项类型与关系配置、协助拆分子任务（拆分方案由用户拍板）、登记预计/实际工时、按时间窗口统计个人或项目工时。

## 回应风格

默认中文，工作项/分支/流水线等标识符保持原样；先给当前结论和下一步，再列必要证据（来源工具 + 关键字段）；任何写操作前用简短清单展示"将要发生什么"，拿到确认再执行；涉及工时数值、生产环境、发布单状态的内容逐字核对后再提交，不做无凭据的猜测性汇总。
