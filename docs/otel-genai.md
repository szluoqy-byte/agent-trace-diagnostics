# OTel GenAI 数据接入

工作台统一导入 DeepAgent 和 OTel GenAI，可在一批中混合上传两种来源。TrajDebug 统一 JSON 和标准消息日志用于离线评测，已入库的历史轨迹和诊断记录仍可查看。

## 使用

1. 导出 **OTLP JSON**：顶层 `resourceSpans`，其下 `scopeSpans[].spans[]`。JSONL 每行也必须是完整的 OTLP 批次对象。同文件按 `traceId` 合并批次，每个 GenAI trace 生成一条轨迹；不同文件应分别包含完整会话。
2. 在“导入轨迹”选择文件，声明业务运行成功或失败。Span 的 OK / ERROR 状态不会自动转换为任务结果。
3. 在“调用时序”查看各 span 的起止和重叠，点击调用查看输入和输出。在“诊断步骤”展开“OTel 来源”，查看 trace ID、span ID、属性和消息位置、原始 span、resource、scope 和源批次位置。
4. 点击“开始诊断”，使用原有官方流程。证据跳转、人工复核、历史和报告导出继续可用。

可直接上传 [OTLP GenAI 示例](../examples/otel-genai.json)。它是合成格式示例，包含两次模型调用及一次工具调用，转换为四条消息；不是论文测试集，不会自动调用模型。

## 字段映射

| 来源 | 转换规则 |
|---|---|
| `gen_ai.input.messages` | 从完整输入快照还原历史；后续调用必须保留已有历史作为前缀，只追加新消息，不全局删除相同文本。 |
| `gen_ai.output.messages` | 追加一个已选中的 assistant 候选。 |
| `role`、`name` | 支持 system / user / assistant / tool；输出未指定 name 时使用 `gen_ai.agent.name`。 |
| `gen_ai.agent.name`、`gen_ai.agent.id` | 识别 Agent，优先使用入口 `invoke_agent` span。建议归属读取关键步骤的来源 span 或其父链。存在稳定 ID 时用于区分同名 Agent。`service.name` 保留为资源属性。 |
| `parts` | 支持 text、tool_call、tool_call_response；以确定性的 JSON 文本保留工具调用 ID、名称、参数和结果。也兼容文本 `content`。 |
| `gen_ai.system_instructions` | 转为 system 消息，与输入中相同的首条 system 消息合并来源。 |
| `gen_ai.tool.definitions` | 通过已有 runtime_inputs 在该次模型决策步骤提供；后续才出现的定义不提升到全局任务描述。 |
| `execute_tool` span | 原样保存。只有结果已出现在后续模型输入中，且 ID、结果和时间匹配时，才附加工具 span 引用；不假设工具执行结果已经被 Agent 看见。 |
| 其他属性、状态、resource / scope | 保存供核对和导出，不作为额外提示上下文。 |

支持 OTLP AnyValue 的 arrayValue / kvlistValue 结构，也兼容将消息属性编码为 JSON 字符串的 instrumentation。没有输出时仅保留真实输入并显示提示，不合成回答。

## 官方契约与存储

依据 [官方接入契约](https://github.com/THU-KEG/TrajDebug/blob/5b04f98b1995ec758d9aba70940d17bb7d2d169b/data_processing/README_zh.md#L73-L92)：

```text
OTLP JSON / JSONL → GenAI 转换器 → 工作台轨迹与来源映射
  → 官方 messages + metadata JSON → 官方 validate_unified → 现有五阶段诊断
```

工作台上传的文件由后端转换器处理。执行前，`to_unified` 使用固定提交的 `data_processing/schema.py::validate_unified` 校验诊断输入。后台沿用固定版本的五阶段实现，并使用任务选定的提示词语言。

`step` 从 0 连续编号；assistant 可诊断，user / system / tool 不作为错误归因候选；tool 使用官方 `compress_role=compress`。annotation 为空，业务结果映射为 reward 0/1。未知结果可暂存，须明确结果后才能诊断。

原始 span 和 source_refs 单独保存在工作台中，在 `to_unified` 时剔除。完整 JSON 报告保留这些来源；Markdown 包含逐步骤的 span 映射。轨迹摘要处可以下载原始 span 来源包。

## 支持范围

- 支持有完整输入历史的串行文本与工具调用会话。按纳秒时间排序并验证历史连续性；外层 Agent 与内层调用都记录消息时使用叶级调用，保留外层 span。
- 并发模型分支、多个未选定候选、跨会话混合、截断或改写的历史、图片/音视频和不透明消息部分会明确拒绝导入。
- 仅有耗时、token、模型名或外部内容引用的 trace 无法诊断；需要实际消息。应在原采集系统开启内容采集；本项目没有新增业务 SDK、Protobuf 或在线 OTLP 接收端。
- 格式适配不扩展官方算法的并发因果建模能力，也不代表复现论文指标。

字段参考 [GenAI 输入模型](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/gen-ai-input-messages.json) 和 [输出模型](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/gen-ai-output-messages.json)。约定仍在发展，当前适配器版本为 `otel-genai-v1`。
