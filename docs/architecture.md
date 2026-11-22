# TrajDebug 官方基线接入

官方来源：https://github.com/THU-KEG/TrajDebug

固定提交：`5b04f98b1995ec758d9aba70940d17bb7d2d169b`。许可证为 MIT；20 个上游文件保存在 `backend/tracelens/_vendor/trajdebug`，包含原始许可证、引用信息和启动脚本。`UPSTREAM.json` 记录逐文件 SHA-256，每次启动诊断先检查完整性。

## 运行边界

FastAPI / SQLite 队列 → `engine.diagnose` 输入适配 → 独立 Python 子进程 → 原样执行官方五阶段 → 展示适配 → 人工复核与导出。

1. `StageACompressor.process_file`：逐步生成三档压缩；短消息和适用的机器输出由官方逻辑跳过模型压缩。
2. `StageBDetector.process_file`：按官方 `judgable` 筛选，使用分层历史逐步发现错误承诺与冲突依据。
3. `Phase1Clusterer.process_file`：将触发点归入错误实例。
4. `Phase2Annotator.process_file`：逐实例复核承诺强度、有效起点、修复状态及终局联系。
5. `Phase3Assembler.assemble_file`：保留官方默认 LLM 裁决、候选外选择和机械回退。

诊断五阶段内部不增加约束生成、自然语言断言或独立前缀复核；建议使用可选的官方反馈后处理。

## 本地适配做什么

- 保留消息顺序、工具调用文本、原始步骤索引、原生 `judgable` / `compress_role` 和框架说明。缺失路由字段时使用官方默认行为。
- 人工标注和隐藏模拟器指令不进入流水线输入。运行结果必须明确为成功或失败；未知结果不会默认当作失败。
- 已明确声明对 Agent 可见的运行时输入，在其可见时间处注入；不会把后续输入提前放进全局任务描述。
- OpenAI 兼容接口、模型、超时、SDK 重试和步骤并发由本地环境变量配置。模型输出上限默认与官方脚本一致为 32768；步骤并发默认为 8。上游提示、解析、重试、状态和归因规则保持原样。
- 聚类阶段默认通过官方 `judgement_force_json=false` 配置关闭强制 JSON；官方该阶段提示未包含 json 关键词，直接开启会被 DeepSeek 拒绝。保留原提示和解析器；`DIAG_CLUSTER_JSON_MODE` 可显式切换，缓存按该参数隔离。
- 通过 HTTP 事件钩子计数实际请求与服务端 token；缓存命中单独计数。推理 token 是输出 token 的分项，不再次相加。
- 每次运行保存输入、五阶段原始 JSON、阶段用量和参数。API Key 只通过进程环境传入，不写入运行配置或前端响应。
- 展示层按 `official_views_v1` 定位官方引文，分别标记原始输入、官方压缩视图和来源未定位。Stage B 引用按当前步 th1、历史步骤角色与距离路由定位；无法确定检测焦点的环境回指及生命周期引用保留视图匹配位置供复核。引用不因无法匹配原始日志而被删除或判为算法错误。
- 原文偏移和压缩视图偏移分开保存；压缩偏移不用于高亮原始日志。前端与 Markdown 导出均展示引用来源，压缩视图可展开并对照原始消息。离线评估分别统计 `original_matched` 和 `compressed_matched`，不将两者混称为原文匹配率。
- 非法官方步骤在展示层显示为未确定，原始预测仍保留。候选外合法步骤正常展示，不伪造关联实例。

## 版本与历史

官方反馈通过 `feedback.py` 与隔离的 `feedback_worker.py` 执行原样保存的 `applications/generate_feedback.py`。与诊断共享工作槽，单独存入 `feedback_runs` 并复制原运行产物；不将人工复核、标注答案或 OTel 原始元数据补入建议提示。失败重试保留产物并使用新缓存空间，原始诊断保持独立。详见 [官方改进建议](feedback.md)。

OTel GenAI 通过独立的 `otel_genai.py` 增量接入，`parse_upload` 自动识别 OTLP 信封；原解析路径保留。转换为工作台消息后，共用 `to_unified`，对新增格式调用原样保存的官方 schema 校验。来源 refs 和 raw span 不进入官方输入；格式适配本身不改变既有轨迹数据结构。详见 [接入说明](otel-genai.md)。

当前结果使用 `schema_version=2`，引用展示版本为 `official_views_v1`。恢复旧报告时，前端将其显示为历史诊断并引导重新运行；已经清理的历史数据在 `data/backups`。人工复核以单独记录保存，不覆盖模型或官方原始输出。

这次以固定官方代码为准，并非将论文中的每一条规则重新实现。例如官方代码的预算判据和最终裁决策略与论文描述存在差异，工作台不混用两套口径。接入测试与单条模型冒烟验证不等于论文效果复现。


## 任务工作台的持久化边界

`task_store.py` 在既有 traces / jobs / reviews / feedback_runs 外新增任务成员关系、分析轮次与请求映射。轨迹模型中的 `task_id` 仍是官方轨迹标识，不被容器任务 ID 取代。相同内容只存一份，成员关系与诊断作业按任务 / 轮次隔离。

`task_api.py` 提供真实解析器的上传校验、原子建任务、后端筛选分页、批量操作、历史只读与范围导出。`execution.py` 保存非敏感执行配置；重试沿用该轮配置，模型 Key 仍取后端环境。队列共享原有并发槽，自动建议也调用相同的官方反馈入口，不引入新算法阶段。

旧数据通过一次性幂等迁移归入历史任务，原 jobs、reviews、feedback_runs 与运行文件均保持原样。迁移前配置不完整的记录明确注明，未执行项冻结迁移时的系统配置。
