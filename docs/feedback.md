# 单轨迹改进建议

## 使用流程

完成业务失败轨迹的诊断并定位到关键步骤后，在轨迹详情打开「改进建议」。Fast、Standard、Deep 通过同一入口为本次诊断生成一份建议。启动任务时勾选自动生成，可在每条轨迹完成诊断后按相同条件触发。

系统优先采用人工确认或修改后的结论，否则使用综合结论或单引擎诊断。暂定结论会在建议中显示待复核。无法定位有效步骤、原文为空或复核已驳回时，先重新诊断或复核。

建议包含问题、优先级、具体改动与回归验证方式，可从步骤编号跳回原文。人工复核变化后可以重新生成。建议和原始诊断分别保存，完整 JSON 包含 `feedback` 与 `feedback_history`，Markdown 包含改进建议和生成用量。

## 历史 TrajDebug 单路反馈

历史 TrajDebug 单路诊断使用固定提交 `5b04f98b1995ec758d9aba70940d17bb7d2d169b` 的 `applications/generate_feedback.py`，通过 `UPSTREAM.json` 校验 SHA256 与 Git blob。反馈提示词、统计、分类、解析和重试由官方模块执行。已有官方建议仍可查看、复制和导出。

```text
已完成诊断的 input.json / input_final.json / 可选 input_stage_a.json
  → 独立建议任务中的产物副本
  → 官方 _process_one
  → _build_report_prompt → _llm_report_call → _assemble_report
  → input_report.json + 用量、来源和展示字段
```

主建议来自 `fix_suggestion.hint_sentence`，过程总结来自 `fix_suggestion.narrative_summary`，替代候选来自 `alternative_critical_steps`。官方原始报告原样保留；前端展示字段只做类型和空值处理，不替换原诊断步骤、分类、证据或人工复核。

历史官方反馈要求失败轨迹、有效关键步骤与完整的诊断产物。生成前校验输入的 SHA256、最终产物与已保存诊断是否一致。

## 任务、成本和重试

- 正常情况下每条建议任务发起一次模型请求，生成失败时的调用或解析重试可能增加请求。复用所属诊断的模型连接、提示词语言及统一的模型并发额度。
- 建议与诊断共用 `DIAG_CONCURRENCY` 个工作槽。单独记录 queued / running / completed / failed，不改变原诊断任务状态。
- 重复点击复用已排队、运行中或完成的同一份建议。失败重试会新建记录，保留旧记录与产物。
- 任务保存在 SQLite 的 `feedback_runs`。重启保留队列，运行中断标记为失败并允许重试。
- 历史官方反馈的运行副本和报告保存在 `data/trajdebug/feedback/<feedback_id>/`，每次手动重试使用独立缓存空间。

## API

- `POST /api/jobs/{job_id}/feedback`：生成、复用或在失败后重试。
- `GET /api/jobs/{job_id}`：包含最新建议任务、可用性与原因。
- `POST /api/jobs/{job_id}/feedback/regenerate`：以当前结论重新生成，需提供当前已完成的 `feedback_id`。
- `GET /api/jobs/{job_id}/feedback/export`：下载单轨迹建议 JSON。历史官方反馈继续导出官方原始报告。
- 原有诊断导出接口继续可用，并附带独立的建议数据。

此能力生成改进反馈，不包含自动修改业务代码、自动应用提示词或优化收益评估。

## 接入验证

2026-11-18：后端测试覆盖官方脚本执行、原格式与 OTel 输入、去重、重启、取消、失败重试和导出；浏览器覆盖桌面与手机布局、复制、原始 JSON 下载及持久化。已有“航空 · 03”诊断已使用 DeepSeek 生成一份真实官方反馈，新增一次模型请求，无失败；原有 20 条轨迹及原诊断结果保留。此验证只确认接入可用，未评估建议应用后的任务成功率。
