# TrajDebug 接入与运行参考

系统整体结构见 [系统架构](architecture.md)。本文用于核对算法基线、运行产物和证据口径。

后台基于 TrajDebug 固定提交 `5b04f98b1995ec758d9aba70940d17bb7d2d169b`。项目内的上游目录附有 MIT 许可证、引用信息、启动脚本和哈希清单。诊断开始前校验源码完整性。 [上游仓库](https://github.com/THU-KEG/TrajDebug)

## 运行边界

FastAPI / SQLite 队列 → `engine.diagnose` 输入适配 → 独立 Python 子进程 → 运行固定版本的官方五阶段 → 展示适配 → 人工复核与导出。

1. `StageACompressor.process_file` 逐步生成三档压缩；短消息和适用的机器输出由官方逻辑跳过模型压缩。
2. `StageBDetector.process_file` 按官方 `judgable` 筛选，使用分层历史逐步发现错误承诺与冲突依据。
3. `Phase1Clusterer.process_file` 将触发点归入错误实例。
4. `Phase2Annotator.process_file` 逐实例复核承诺强度、有效起点、修复状态及终局联系。
5. `Phase3Assembler.assemble_file` 保留官方默认 LLM 裁决、候选外选择和机械回退。

改进建议作为独立的官方反馈任务，在诊断完成后按需运行。

## 本地适配做什么

- 保留消息顺序、工具调用文本、原始步骤索引、原生 `judgable` / `compress_role` 和框架说明。缺失路由字段时使用官方默认行为。
- 人工标注用于离线评分。诊断前须明确业务运行成功或失败，模型输入使用 Agent 当时可见的消息和运行时输入。
- 运行时输入按 Agent 首次可见的步骤进入诊断消息。
- OpenAI 兼容接口、模型和运行参数由系统设置或环境变量提供。执行阶段使用任务选定的中文或英文提示词，英文沿用上游提示。默认值见 [配置说明](../README.md#输入与配置)。
- 聚类阶段默认使用普通文本模式。需要强制 JSON 时可通过 `DIAG_CLUSTER_JSON_MODE` 切换。两种模式使用独立的阶段缓存。
- 通过 HTTP 事件钩子计数实际请求与服务端 token。缓存命中单独计数，推理 token 计入输出 token。
- 每次运行保存输入、五阶段原始 JSON、阶段用量和参数。模型凭据由服务端持有，API Key 和自定义 Header 通过进程环境传给诊断进程。
- 展示层按 `official_views_v1` 定位官方引文，分别标记原始输入、官方压缩视图和来源未定位。Stage B 引用按当前步 th1、历史步骤角色与距离路由定位；无法确定检测焦点的环境回指及生命周期引用保留视图匹配位置供复核。未能定位的引用仍保留，供人工核对。
- 原文和压缩视图各自保存引文偏移，用于在对应来源中高亮。前端与 Markdown 导出均展示引用来源，压缩视图可展开并对照原始消息。离线评估分别统计 `original_matched` 和 `compressed_matched`。
- 工作台保存官方原始预测。对非可诊断的预测步骤，报告层可按已记录的因果投影规则回指前一步，或显示未确定。候选外合法步骤正常展示。详见 [算法优化日志](algorithm-optimization-log.md)。

## 版本与历史

官方反馈通过 `feedback.py` 与隔离的 `feedback_worker.py` 执行原样保存的 `applications/generate_feedback.py`。建议与诊断共享工作槽，单独存入 `feedback_runs`，输入来自原有诊断产物。建议重试保留历史产物，并使用新的缓存空间。详见 [官方改进建议](feedback.md)。

工作台接收 DeepAgent 与 OTel GenAI。转换后的轨迹经过共用的 `to_unified` 入口及上游契约校验。来源引用、原始 span 和评测标注由工作台或离线评估单独使用。详见 [OTel GenAI 接入说明](otel-genai.md)。

当前结果使用 `schema_version=2`，引用展示版本为 `official_views_v1`。旧格式报告作为历史诊断展示，支持重新运行。人工复核和算法输出各自保存。

预算判据和最终裁决以固定版本的上游代码为准。准确率与回归结果以 [离线评估](rsi-evaluation.md) 为准。
