# 官方改进建议

## 使用流程

完成一次真实的失败诊断后，打开右侧“改进建议”页签，点击“生成改进建议”。已有且运行产物完整的诊断也可直接使用，不必重跑五阶段。原格式和 OTel GenAI 使用同一入口。

生成后可以查看、复制可加入 Agent 系统提示词的指令，展开失败过程总结和替代根因候选，下载官方反馈 JSON。完整 JSON 报告包含独立的 feedback、feedback_history；Markdown 报告附加改进建议和生成用量。

建议基于该次模型原始诊断。人工复核仍独立保存；修改或驳回模型结论时，界面会提醒建议未随复核改写。建议尚未自动应用，也未通过业务 Agent 重跑验证。

## 官方实现

原样引入同一固定提交 `5b04f98b1995ec758d9aba70940d17bb7d2d169b` 的 `applications/generate_feedback.py`，并加入 `UPSTREAM.json` 的 SHA256 与 Git blob 校验。反馈提示词、统计、分类、解析和重试由官方模块执行；本地适配层不另写建议生成提示词。

```text
已完成诊断的 input.json / input_final.json / 可选 input_stage_a.json
  → 独立建议任务中的产物副本
  → 官方 _process_one
  → _build_report_prompt → _llm_report_call → _assemble_report
  → input_report.json + 用量、来源和展示字段
```

主建议来自 `fix_suggestion.hint_sentence`，过程总结来自 `fix_suggestion.narrative_summary`，替代候选来自 `alternative_critical_steps`。官方原始报告原样保留；前端展示字段只做类型和空值处理，不替换原诊断步骤、分类、证据或人工复核。

成功轨迹、没有有效关键步骤、演示、旧融合报告、版本不同或缺少原始运行产物时，不创建建议任务。输入的 SHA256、最终产物与已保存诊断不一致时也会拒绝。

## 任务、成本和重试

- 正常情况下每条建议任务新增一次 LLM 报告生成；官方的调用或解析重试可能增加请求。使用已有 OpenAI 兼容接口配置。
- 建议与诊断共用 `DIAG_CONCURRENCY` 个工作槽。单独记录 queued / running / completed / failed，不改变原诊断任务状态。
- 重复点击复用已排队、运行中或完成的同一份建议。失败重试会新建记录，保留旧记录与产物。
- 官方 APIModel 会缓存成功 HTTP 响应，包括无法解析的模型文本。每次手动重试使用独立缓存空间，避免反复命中失败响应；没有修改官方缓存实现。
- 任务保存在现有 SQLite 的新增表 `feedback_runs`；重启时保留队列，运行中断会标记失败并允许重试。旧轨迹和诊断数据无需重建。
- 运行副本、参数和官方报告保存在 `data/trajdebug/feedback/<feedback_id>/`。API Key 仅经进程环境传入。

## API

- `POST /api/jobs/{job_id}/feedback`：生成、复用或在失败后重试。
- `GET /api/jobs/{job_id}`：包含最新建议任务、可用性与原因。
- `GET /api/jobs/{job_id}/feedback/export`：下载成功生成的官方原始反馈 JSON。
- 原有诊断导出接口继续可用，并附带独立的建议数据。

此能力生成改进反馈，不包含自动修改业务代码、自动应用提示词或优化收益评估。

## 接入验证

2026-11-18：后端测试覆盖官方脚本执行、原格式与 OTel 输入、去重、重启、取消、失败重试和导出；浏览器覆盖桌面与手机布局、复制、原始 JSON 下载及持久化。已有“航空 · 03”诊断已使用 DeepSeek 生成一份真实官方反馈，新增一次模型请求，无失败；原有 20 条轨迹及原诊断结果保留。此验证只确认接入可用，未评估建议应用后的任务成功率。
