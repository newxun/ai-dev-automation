# Design Fidelity Repair

## Problem

蓝湖 MCP 或设计稿图片辅助开发后，页面仍可能存在元素缺失、主要区域错位、层级错误或明显样式偏差。
原开发会话同时承载需求理解、编码和调试，视觉复查容易受既有判断影响；单纯让人做最终验收又会把
大量可机械修复的问题留给人工。

## Goal

在独立、专注的流程中重新读取原始设计源，用浏览器采集真实实现，交给干净上下文做视觉诊断，
由主 Agent 修复后重新截图验证。它是设计还原开发循环，不是最终 UI 验收。

## Use Cases

- 使用蓝湖 MCP 开发后，希望重新核对蓝湖并修复明显还原偏差。
- 用户提供设计稿图片，需要与真实运行页面直接对比并修复。
- 用户没有再次提供设计稿，希望从 development-readiness 任务文档自动找到来源。
- 希望视觉诊断和代码修改分离，避免实现者自证正确。

## Not For

- 修改前与修改后的视觉回归。
- 没有外部设计基准的主观 UI 美化。
- 完整功能测试、代码 Review 或最终场景验收。
- 像素级差异评分。

## Contents

- [`SKILL.md`](SKILL.md)：入口、硬边界、来源路由和主循环。
- [`reference/methodology.md`](reference/methodology.md)：设计源、浏览器截图、独立诊断与修复方法。
- [`reference/protocols.md`](reference/protocols.md)：授权、安全、异常降级和报告规则。
- [`report-template.md`](report-template.md)：单次修复报告模板。

## Inputs

至少需要：

- 可运行的 UI 实现及目标页面入口。
- 蓝湖链接或设计稿图片；未显式提供时可从当前任务的 development-readiness 报告及 `raw/` 材料查找。
- 当前任务需求，用于区分 UI 呈现与行为逻辑。

## Example

用户：

```text
这个页面已经开发完成，请做设计还原修复。设计稿信息在本任务的就绪文档里。
```

预期执行：

1. 根据任务 slug 找到最新就绪文档中的蓝湖链接。
2. 通过蓝湖 MCP 重新读取目标画板，而不是复用开发时的文字总结。
3. 启动页面，以设计画板视口截取初始实现。
4. 独立视觉 Agent 比较设计画面和截图，主 Agent 合并结构与视觉问题。
5. 修复确定的表现层偏差并运行相关功能测试。
6. 重新截图，由新的独立上下文复查；最多三轮。
7. 输出修复报告，保留无法裁决的设计/需求冲突，不写“最终 UI 验收通过”。

## Outputs

`docs/design-fidelity-repair/tasks/<timestamp>-<task-id-or-slug>.md`

报告引用的设计画面、修复前后截图和独立诊断结果默认保存在
`docs/design-fidelity-repair/evidence/<timestamp>-<task-id-or-slug>/`，确保后续场景验收仍可读取。

## Limitations

- 蓝湖 MCP、浏览器或独立视觉 Agent 不可用时可能只能部分完成。
- 单张设计图不能证明未展示的响应式和交互状态。
- 模型适合发现结构性和明显视觉差异，不适合稳定裁决细微像素差异。
- 最终 UI 验收仍由人完成。
