# 部署协助：发起流水线 / 排查失败日志 / 处理发布冲突

> 由 `SKILL.md` 引用。目标：把云效流水线/发布单的运行状态和日志转成可执行的修复线索，在触发有外部副作用的动作前强制走确认。

## 步骤

1. **定位流水线**：已知名称/ID 直接 `get_pipeline`；不确定就用 `list_pipelines` 或 `smart_list_pipelines`（支持"最近一次""昨天失败的"这类自然语言时间描述）缩小范围。
2. **查看运行状态**：`get_latest_pipeline_run` 看最新一次是否失败；需要历史对比时用 `list_pipeline_runs`；`get_pipeline_run` 看单次运行的阶段详情。
3. **定位失败日志**：先用 `list_pipeline_jobs_by_category` 找到失败所在的具体 Job/阶段，再用 `get_pipeline_job_run_log` 拉该 Job 的日志；`list_pipeline_job_historys` 可以看这个 Job 最近多次执行是否是偶发问题。
4. **应用交付/机器部署场景**的日志入口不同：VM 部署用 `get_vm_deploy_order` + `get_vm_deploy_machine_log`；AppStack 发布单用 `get_change_order` + `list_change_order_job_logs` + `find_task_operation_log`（通常包含下游部署引擎的调度细节）；`get_machine_deploy_log` 用于机器维度的部署日志。
5. **归类失败原因**再决定下一步，不要看到"失败"就直接重跑：
   - 编译/测试失败 → 日志里的报错栈是主要证据，交给编码环节修复，不在 MCP 层面处理。
   - 环境/依赖/连通性问题 → 检查是否是资源或服务连接配置问题（`list_service_connections` 可看组织内已配置的服务连接）。
   - 审批/人工卡点未通过 → 应用交付里常见（`get_appstack_change_request_audit_items` 看审批项），不能绕过审批直接执行下一步。
   - 代码合并冲突 → 用 `get_compare` 对比双方分支差异，配合 `list_change_request_comments` 看评审方的意见；**不要**在 MCP 层面直接"自动解决冲突"——冲突涉及双方修改意图，先把冲突文件内容（`list_files` + `get_file_blobs`）展示清楚，合并策略交给用户或编码环节人工确认后再改动。
6. **高风险动作前必须先给确认清单再执行**，覆盖：`create_pipeline_run`、`execute_pipeline_job_run`、`update_pipeline` / `create_pipeline_from_description`、`stop_vm_deploy_order` / `skip_vm_deploy_machine` / `retry_vm_deploy_machine` / `resume_vm_deploy_order`、`execute_job_action`、`create_change_order`、`create_appstack_change_request` / `cancel_appstack_change_request` / `close_appstack_change_request`、`execute_app_release_stage` 及各类 `*_release_stage_*`（`retry_app_release_stage_pipeline`、`skip_app_release_stage_pipeline`、`pass_app_release_stage_validate`、`refuse_app_release_stage_validate` 等）。确认清单至少包含：目标环境（含是否生产）、目标分支/版本/制品、预期影响范围、失败或误触发后的回滚方式。
7. **处理完成后视需要回写**：在合并请求或工作项下用 `create_change_request_comment` / `create_work_item_comment` 记录处理结果，文案先给用户确认。

## 人工确认点

- 任何会触达生产环境、审批流程或对外发布的动作，一次用户同意只覆盖当前这一次调用，不能推广到"以后失败了就自动重跑"。
- 代码冲突的合并策略必须由人（或编码环节）决定，Agent 只负责把双方差异证据整理清楚。
- 审批类动作（`pass_app_release_stage_validate` / `refuse_app_release_stage_validate`）本质是业务决策，Agent 不应替用户判断是否通过，只能协助整理审批所需的证据。

## 常见误区

- 只看到"运行失败"就直接调用重跑类工具，而不先定位失败 Job 和日志——重跑可能只是重复暴露同一个问题，还可能带来重复的外部副作用（比如重复触发下游部署）。
- 把 `smart_list_pipelines` 的自然语言检索结果当成精确匹配，实际执行动作前应该用 `get_pipeline` / `get_pipeline_run` 核对目标是否正确。
- 应用交付里的部署单状态和流水线状态是两套系统，排查时不要混用彼此的工具（发布单相关问题走 `application-delivery` 的 `*_change_order*` / `*_release_stage*` 工具，纯流水线执行问题走 `pipeline-management` 的 `pipeline_run` / `pipeline_job` 工具）。
