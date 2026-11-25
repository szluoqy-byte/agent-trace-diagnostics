# AgentDebugX 接入与运行参考

工作台内置 AgentDebugX 0.5.2 源码，固定到提交 `e180ac2e46e4c1e468336526f67ad122ecaf34ff`。后端在独立进程校验后运行官方检测、DeepDebug 归因和恢复建议。 [官方发行版](https://github.com/AgentDebugX/AgentDebugX/releases/tag/v0.5.2)

## 使用

新建任务时选择「AgentDebugX」或「双路诊断」，设置提示词语言后开始分析。已有任务可从「重新分析」为新一轮选择诊断方式。旧分析记录保留各自的配置和结果，默认方式仍为「标准诊断」。

单路 AgentDebugX 在「证据与建议」展示检测发现、根因原文和恢复建议。双路任务额外展示「双路对照」及两路运行状态。可下载 AgentDebugX 原始检测和诊断报告。恢复建议为只读报告内容，业务 Agent 的真实重跑独立于工作台诊断。

失败详情会区分模型接口状态码、连接超时、输出截断、返回格式异常和运行器异常。双路运行时可以在「双路对照」查看 AgentDebugX 的失败原因及另一条引擎的结果。错误详情只保存工作台生成的提示。

## 集成边界

- DeepAgent 和 OTel GenAI 继续由现有适配层导入。AgentDebugX 的每个诊断事件回指同一份轨迹中的消息步骤，已采集的父子关系和 Agent 名从来源记录提取。
- 新一轮分析使用系统设置中的默认模型。双路诊断使用同一模型连接，执行时读取该连接的 API Key 和自定义 Header。自定义 `Authorization` 优先于由 API Key 生成的 Bearer 认证头。AgentDebugX 使用轮次保存的请求超时、重试次数和输出上限，单次输出同时受官方阶段预算约束。步骤分析并发和聚类 JSON 模式用于 TrajDebug 的对应阶段。
- 模型地址可配置为 Base URL 或完整的 `/chat/completions` 地址。两套引擎默认直连模型服务。需要继承服务器代理配置时，可在服务进程环境中设置 `DETECTOR_HTTP_TRUST_ENV=true`。
- 英文逐字沿用上游提示词。中文按全局归因、区间排查、候选选择、分歧裁决和最终结论等阶段装配。中英文共享诊断流程、JSON 字段和值域，工具名、事件 ID 和证据原文保持原样。
- 两路分别运行并保存原始结果。对照只判断步骤能否对齐及原文证据是否可定位，人工复核仍负责确认根因。双路报告进入失败模式分析前需完成复核，并满足原有归因和证据条件。

## 源码与模板维护

- 上游代码和许可证位于 [源码目录](../backend/tracelens/_vendor/agentdebugx/)。[完整性清单](../backend/tracelens/_vendor/agentdebugx/UPSTREAM.json) 记录固定提交、随项目发布的文件哈希，以及被修改文件的上游哈希。
- [提示词接入补丁](../backend/tracelens/_vendor/agentdebugx/PROMPT_TEMPLATES.patch) 集中记录模板选择入口。中文文案在 [中文提示词目录模块](../backend/tracelens/diagnosis/agentdebugx_prompts.py) 中独立维护。报告和运行快照分别记录采用的模板版本。
- 升级上游时，从目标提交导出 `src/agentdebug` 与许可证，在暂存目录重放补丁，再更新版本常量和 SHA-256 清单。用本地模型替身验证英文请求、中文分阶段模板、证据回指、双路部分失败和原始报告下载。准确率验收沿用独立的 RSI 流程。

工作台的组件关系见 [系统架构](architecture.md)。
