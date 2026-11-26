# 天罗智能轨迹诊断

基于 [TrajDebug 官方代码](https://github.com/THU-KEG/TrajDebug/tree/5b04f98b1995ec758d9aba70940d17bb7d2d169b) 的本地 Agent 轨迹诊断项目。前端采用中文研发工作台，后端直接执行固定官方版本的五阶段流水线。

新建任务时可选标准诊断、AgentDebugX 或双路诊断，并选择中文或英文提示词。双路模式在单条轨迹详情中展示两路原始结论和证据对照。 [AgentDebugX 接入说明](docs/agentdebugx.md)

用户流程为**分析任务列表 → 上传与校验 → 任务详情 → 单条证据与复核**。任务详情展示当前分析结果和配置。整体设计见 [系统架构](docs/architecture.md)。

## 启动

需要 Python 3.11+、Node.js 20.19+ 和 pnpm。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
cp .env.example .env
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
bash scripts/start.sh
```

访问 http://127.0.0.1:8017 。在「系统设置」添加 OpenAI 兼容模型连接、测试连接并设为默认。已有 .env 时直接沿用，其中已配置的模型会显示为「环境配置」。

开发模式使用 `bash scripts/dev.sh`，Vite 地址为 http://127.0.0.1:5173 。

### Windows PowerShell

在仓库根目录运行以下命令。`.env` 使用 UTF-8 编码，已有配置会保留。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Push-Location frontend
pnpm.cmd install --frozen-lockfile
pnpm.cmd build
Pop-Location
.\scripts\start.ps1
```

## 使用

从左侧导航的「轨迹库」可跨任务筛选已有轨迹、查看原文和历史诊断，也可跨页勾选轨迹新建分析任务。保存任务时写入的轨迹会直接出现在库中。

在任务详情打开「任务改进建议」，可按当前分析的关键失败步骤所属的 Agent 生成改进和回归验证建议。报告显示纳入量、未纳入原因和可跳转的代表证据。轨迹库与任务轨迹列表支持按自动识别的入口 Agent 搜索。历史失败模式分析仍可查看和导出。

1. 点击「新建分析任务」，填写排查目标并上传 DeepAgent 或 OTel GenAI 轨迹文件。也可从轨迹库选择已有轨迹。
2. 检查逐文件校验结果，排除错误文件，补齐未知业务结果。上传不调用模型；可保存草稿，或创建并开始分析。DeepAgent 与 OTel GenAI 可以混合使用。
3. 在任务详情查看业务结果、诊断进度、逐条建议和人工复核。轨迹默认每页 10 条，可切换 20 / 50 条；跨页保留勾选，表头只全选本页，更改筛选会回到第一页并清空勾选。
4. 在单条详情的「综合分析」查看总览和各引擎结果，点击关键失败步骤跳转原文。支持在筛选范围内跨页切换上一条 / 下一条，返回时定位到当前轨迹所在页。
5. 在「改进建议」为当前轨迹按需生成一份改动和回归验证建议，也可在启动任务时选择诊断后自动生成。建议与诊断分别计量和保存。
6. 确认、修改或驳回结论并记录验证计划，导出单条 Markdown / JSON、官方原始输出，或整轮及所选轨迹的 JSON。
7. 「重试失败」重试当前分析的失败项并保留请求历史。「重新分析」使用当前配置更新任务结果。复制任务可复用轨迹数据。

改进建议适用于已定位关键步骤的业务失败轨迹。应用建议前请人工核对并验证效果。详见 [改进建议](docs/feedback.md)。

## 算法基准

固定官方提交 `5b04f98b1995ec758d9aba70940d17bb7d2d169b`，源码和 MIT 许可证随项目保存，校验清单为 `backend/tracelens/_vendor/trajdebug/UPSTREAM.json`。

- Stage A：逐消息三粒度压缩。
- Stage B：逐步错误触发检测。
- C Phase 1：错误实例聚类。
- C Phase 2：承诺强度、有效起点、修复和终局联系。
- C Phase 3：关键失败归因，保留官方默认 LLM 裁决与回退。
- 可选后处理：官方 `applications/generate_feedback.py` 生成改进指令与反馈报告，不覆盖五阶段原始结果。

后台沿用固定上游的五阶段，工作台负责轨迹适配、提示词语言、进程隔离、用量记录和结果投影。算法变更及其验证见 [TrajDebug 算法优化维护日志](docs/algorithm-optimization-log.md)。

## 输入与配置

离线评估使用以下官方统一格式。工作台上传使用 DeepAgent 或 OTel GenAI 轨迹。

```json
{
  "messages": [
    {"step": 0, "role": "user", "content": "先查询，不要提交退款", "judgable": false},
    {"step": 1, "role": "assistant", "content": "create_refund()", "judgable": true}
  ],
  "metadata": {
    "dataset": "custom",
    "task_id": "example",
    "task_description": "先查询，不要提交退款",
    "reward": 0,
    "annotation": {},
    "extra": {}
  }
}
```

离线路径保留原生 `judgable`、`compress_role` 和 `metadata.extra.agent_framework_description`。工作台新建任务时需要补齐未知的业务结果。人工标注独立用于评估。

工作台支持 **DeepAgent 轨迹 JSON** 和 **OTel GenAI 的 OTLP JSON / JSONL**，可在新建任务或轨迹库中混合上传。适配层保留原始 span，并生成统一的调用展示数据、诊断消息和证据映射。TrajDebug 在后台消费通过统一格式校验的消息序列。标准 JSON / JSONL 用于离线评测和回归，历史轨迹可继续查看和复用。OTel 的具体采集要求见 [OTel GenAI 接入说明](docs/otel-genai.md)。

两种 span 来源共用「调用时序」「调用列表」「诊断步骤」。有有效起止时间时默认打开时序，父调用在子调用前展示，同层按开始时间排序。列表与时序共用调用编号，输入和输出在同一调用详情内。诊断编号沿用算法消息顺序，支持从证据跳到原文和对应调用。缺少 span 的历史记录直接展示诊断步骤。

轨迹文件支持 UTF-8（可带 BOM）及带 BOM 的 UTF-16。

| 环境变量 | 用途 | 默认 |
|---|---|---|
| OPENAI_BASE_URL | 环境配置的 OpenAI 兼容接口 | 空，可在系统设置添加模型 |
| OPENAI_API_KEY | 云端密钥；无鉴权本地服务可留空 | 空 |
| DIAG_MODEL | 环境配置的模型标识 | 空，可在系统设置添加模型 |
| DIAG_CONCURRENCY | 同时诊断的轨迹数 | 2 |
| DIAG_LLM_CONCURRENCY | 工作台共享的模型请求并发 | 8 |
| DIAG_MAX_TOKENS | 模型输出上限 | 32768 |
| DIAG_CLUSTER_JSON_MODE | 聚类阶段强制 JSON（默认关闭以兼容 DeepSeek） | false |
| DIAG_TIMEOUT | 模型请求超时秒数 | 120 |
| DIAG_MAX_RETRIES | SDK 瞬时请求重试 | 2 |
| TRACELENS_DATA_DIR | 数据与运行产物 | 未设置时为 ./data，所附配置文件使用 ./data/production |
| TRACELENS_ENABLE_VALIDATION_DATA | 启用评测数据导入接口，仅用于独立验证工作区 | false |

上游自身的解析重试和回退保持原样。实际 HTTP 请求数、服务端输入/输出 token 和缓存命中分别记录。

聚类阶段使用官方已有的普通输出配置：原提示缺少 DeepSeek JSON 模式要求的 json 关键词。该配置保留原提示和官方解析器，其余阶段继续使用官方 JSON 模式。

## 验证

正式工作台从空库启动。需要使用公开评测数据时，在独立数据目录开展验证。以下命令下载固定的 20 条轨迹，并启动验证工作台。验证端口与正式工作台分开。

```bash
TRACELENS_DATA_DIR=./data/validation .venv/bin/python scripts/fetch_samples.py
TRACELENS_DATA_DIR=./data/validation TRACELENS_ENABLE_VALIDATION_DATA=true TRACELENS_PORT=8018 bash scripts/start.sh
curl -X POST http://127.0.0.1:8018/api/samples
```

```bash
.venv/bin/python -m pytest -q
cd frontend
pnpm test
pnpm build
cd ..
# 对已有任务离线评分，不调用模型：
.venv/bin/python scripts/evaluate_trajdebug.py JOB_ID --output data/evaluation/score.json
```

接入测试使用本地 OpenAI 协议替身，执行官方五阶段与反馈模块，覆盖缓存、候选外归因、成功跳过、失败、重试与取消。关键失败步骤定位准确率可使用固定的 200 条精简版或 684 条完整版评估，见 [数据集复现与评分说明](docs/rsi-evaluation.md)。

引用展示已对齐官方压缩视图，分别记录原文、官方压缩视图和来源未定位。来源定位不等同于诊断准确率；引用定位不筛选或改写官方触发、实例及归因。

## 项目文件

- [系统架构](docs/architecture.md)
- [TrajDebug 接入与运行参考](docs/trajdebug-integration.md)
- [用户旅程](docs/user-journey.md)
- [第一轮 RSI 阶段分析](docs/rsi-round-1-analysis.md)
- [TrajDebug 算法优化维护日志](docs/algorithm-optimization-log.md)
- `backend/tracelens/_vendor/trajdebug/`：官方源码、许可证和哈希清单。
- `data/trajdebug/runs/`：每次运行的输入、五阶段原始输出和用量。
- `data/trajdebug/feedback/`：历史 TrajDebug 单路反馈任务的输入副本、官方报告和用量。
- `scripts/official_testset.json`：20 条官方测试轨迹的固定选择、原始路径、文件哈希和标注索引。
- `scripts/prepare_rsi_data.py` 和 `scripts/prepare_rsi_lite.py` 用于生成固定评估集。


## 任务数据与迁移

工作台新增 `analysis_tasks`、`task_traces`、`analysis_runs`、`run_items` 和 `run_attempts`。轨迹继续按内容去重存储，成员关系按任务保存；不同任务分析同一轨迹会创建独立作业。失败重试追加本轮请求，原诊断产物、建议与复核完整保留。

已有轨迹和诊断首次启动时自动归入“历史轨迹 · 已有数据”，迁移幂等且不会重新推断。已有执行配置以原报告为准；未运行项使用迁移时保存的系统配置，界面明确标注。每次启动分析保存当次模型、接口和参数，轮次内重试沿用该连接。页面添加的配置由服务端保存于数据目录中的 `model-connections.sqlite3`，文件权限为 `0600`。密钥不会出现在诊断数据库、报告或配置接口响应中。

上传校验暂存在本地 SQLite，24 小时后失效；单文件最多 10MB、每次最多 20MB、每任务最多 500 条。创建前的所有校验通过后才写入任务。新增 API 为 `/api/import/preview`、`/api/import/samples/preview`、`/api/tasks` 及其 `/runs`、`/traces`、`/actions`、`/export`；原轨迹、诊断、复核与单条导出接口保留兼容。
