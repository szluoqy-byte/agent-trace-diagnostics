# 轨迹诊断准确率迭代

这套数据用于定位关键失败步骤并验证诊断改动。用 `train` 查误判、设计候选改动，用 `dev` 筛选候选，再用冻结的评估集验收。数据可用于本地诊断迭代，按原始发布方的条款使用。文件都在 `data/rsi/v1/`。

| 数据目录 | 轨迹数 | 用途 |
| --- | ---: | --- |
| `inputs/train/` | 238 | 查看轨迹及参考步骤，归纳漏检、候选丢失和选错步骤的原因。 |
| `inputs/dev/` | 80 | 比较迭代版本。由独立评分流程读取标注，向迭代提供分数和阶段统计。 |
| `inputs/heldout/` | 78 | 在选定版本后验收同域准确率。保留同一版本的基线结果，用同一批轨迹复测。 |
| `inputs/external_eval/` | 199 | 用 AgentDebugBench 测跨域准确率，分别报告 ALFWorld、GAIA、WebShop。 |
| `inputs/regression/` | 89 | 保留旧固定测试集、早期开发案例和与它们同组的轨迹，用于独立回归检查。 |

同域数据取自与当前诊断引擎相同提交的 [TrajErrBench 中文版](https://github.com/THU-KEG/TrajDebug/tree/5b04f98b1995ec758d9aba70940d17bb7d2d169b/data/trajerrbench)。按航空、零售和代码分层，目标比例为 60/20/20。同题组整组进同一分区，组由中英文归一化任务描述、SWE 原始实例 ID 及其传递关系确定。英文对照用于分组。τ²-Bench 发布数据中的 ID 标识运行轨迹，任务描述匹配可能漏掉措辞差异很大的同题运行。

跨域集使用 [AgentDebugBench 的统一格式发布版](https://github.com/THU-KEG/TrajDebug/tree/5b04f98b1995ec758d9aba70940d17bb7d2d169b/data/agentdebugbench)。有一条 GAIA 轨迹无关键步骤标签，一条 TrajErrBench 轨迹把关键步骤标在当前引擎适配器排除的步骤上。两条均记录在 `dataset.json` 的排除清单中。数据都来自失败轨迹，本版覆盖关键失败步骤定位。

## 200 条精简版

`data/rsi/v1-lite/` 用于低成本迭代。分为训练 120 条、校准 30 条、同域留出 30 条、跨域评估 20 条。沿用原 100 条及其分区，新增案例优先覆盖不同任务组，并按输入长度和消息步数选样。同一任务组的多次运行只在同一分区内出现。原 100 条归档于 `data/rsi/v1-lite-100/`，历史回归集保存在完整版中。

精简版的四个分区均可分别作为一个任务导入。评分时使用 `data/rsi/v1-lite/labels/<分区>.jsonl`。如已根据精简版留出成绩调参，后续用完整版验收时应取对应分区中尚未用于调参的轨迹。

```bash
.venv/bin/python scripts/prepare_rsi_lite.py --size 200 --source data/rsi/v1 --output /tmp/tianluo-rsi-v1-lite
```

## 使用

1. 在独立验证工作区创建任务，按分区上传 `inputs/<分区>/batch-*.jsonl`。每个文件最多 25 条且低于 8 MiB，可以按文件分批。保留任务与分区的对应关系。
2. 标注独立存于 `labels/<分区>.jsonl`。诊断只读取输入目录。调优过程只查阅训练标签，校准和验收标签用于离线评分。
3. 对已完成的作业离线评分。脚本按数据集、任务 ID 和实际模型输入哈希核对标注。主指标使用关键步骤精确命中率，报告分域结果，并记录未归因和运行失败数量。阶段统计可区分触发漏检、候选保留和最终选择问题。

```bash
TRACELENS_DATA_DIR=./data/validation .venv/bin/python scripts/evaluate_trajdebug.py JOB_ID \
  --labels data/rsi/v1/labels/dev.jsonl
```

对于迭代验收，先在同一模型 `gpt-6-luna` 上固定基线和评分方式。后续保持评估轨迹、引擎版本和模型设置一致，记录每项改动。留出集和跨域集仅用于选定版本的验收。

## 复现划分

`manifest.jsonl` 记录每条轨迹的分区、任务组、原始路径、上游文件哈希和输入批次位置。`dataset.json` 记录分布、排除项和产物校验值。使用新的输出目录生成一份可独立校验的副本。

```bash
commit=5b04f98b1995ec758d9aba70940d17bb7d2d169b
curl -fL "https://codeload.github.com/THU-KEG/TrajDebug/tar.gz/$commit" -o /tmp/trajdebug-rsi-v1.tar.gz
curl -fL "https://api.github.com/repos/THU-KEG/TrajDebug/git/trees/$commit?recursive=1" -o /tmp/trajdebug-rsi-v1-tree.json
.venv/bin/python scripts/prepare_rsi_data.py \
  --archive /tmp/trajdebug-rsi-v1.tar.gz --tree /tmp/trajdebug-rsi-v1-tree.json --output data/rsi/v1
.venv/bin/python scripts/prepare_rsi_lite.py --size 200 --output data/rsi/v1-lite
# 如需历史 100 条版本
.venv/bin/python scripts/prepare_rsi_lite.py --size 100 --output data/rsi/v1-lite-100
```

上游使用条款见 [TrajDebug 数据说明](https://github.com/THU-KEG/TrajDebug/blob/5b04f98b1995ec758d9aba70940d17bb7d2d169b/data/README.md) 和 [AgentDebug 发布方](https://github.com/ulab-uiuc/AgentDebug#agenterrorbench-dataset)。

## 第一轮配对实验

从项目根目录运行以下命令。基线使用工作台当前默认模型，按现有并发参数执行，报告和模型缓存保存在独立实验目录。同一配置下重启命令会跳过已有报告。完整运行会调用模型，离线比较复用这些报告。

```bash
round_dir=data/validation/rsi-round-1
mkdir -p "$round_dir"
cp -n docs/rsi-round-1.md "$round_dir/protocol.md"
.venv/bin/python scripts/run_rsi_baseline.py --output "$round_dir"
.venv/bin/python scripts/evaluate_rsi_round.py --output "$round_dir"
```

在 Windows PowerShell 中从项目根目录运行同一批脚本时，使用 `.\.venv\Scripts\python.exe`。将命令输出重定向到文件时，可先设置 `$env:PYTHONIOENCODING = 'utf-8'`。

候选规则、分区用途和验收门槛见[第一轮约定](rsi-round-1.md)。`report.md` 汇总精确命中，`comparison.json` 给出分域得分和用量，`paired-results.jsonl` 保存逐条预测。后续轮次使用新的输出目录，保留本轮冻结的证据。
