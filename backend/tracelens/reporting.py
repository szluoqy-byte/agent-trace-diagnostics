"""Human-readable export; the raw model output is exported separately unchanged."""

REVIEW_LABELS = {"confirmed": "已确认", "modified": "已修改", "rejected": "已驳回"}
FEEDBACK_LABELS = {"queued": "排队中", "running": "生成中", "completed": "已生成", "failed": "生成失败"}
INSTANCE_LABELS = {
    "manifest_active": "持续影响最终结果",
    "latent_active": "未修复",
    "clean_resolution": "已修复",
    "costly_resolution": "已修复，仍有资源代价",
    "exploration": "探索行为，已排除",
    "unknown": "状态待核验",
}


def markdown_report(trace, job, reviews, feedback=None):
    result = job["result"]
    ai = result["attribution"]
    final = reviews[0]["data"] if reviews else ai
    step = final.get("critical_step")
    lines = [f"# 天罗智能轨迹诊断 · {trace['title']}", ""]
    if result.get("mode") == "demo":
        lines += ["历史合成数据，未运行模型。", ""]
    else:
        lines += [f"诊断模型：{result.get('model') or '未记录'}", ""]
    lines += ["## 当前结论", "", final["summary"],
              f"关键步骤：{f'#{step}' if step is not None else '未确定'} | 根因类别：{final['category']}",
              f"人工复核：{REVIEW_LABELS.get(final.get('decision'), '待复核')}", final.get("notes", ""),
              "", "## 模型原始结论", "", ai["reasoning"]]
    for warning in result.get("warnings", []):
        lines += ["", "提示：" + warning]
    otel = trace.get("data", {}).get("otel")
    if otel:
        lines += ["", "## OTel GenAI 来源", "", f"Trace ID：{otel['trace_id']}",
                  "原始 span 与逐消息来源映射保存在完整 JSON 导出中。"]
        for warning in otel.get("warnings", []):
            lines += ["", "导入提示：" + warning]
        for message in trace["data"]["messages"]:
            refs = message.get("source_refs", [])
            locations = [f"{r['span_id']} · {r['attribute']}" + (f"[{r['message_index']}]" if r.get("message_index") is not None else "") for r in refs]
            lines.append(f"- #{message['step']}：" + "；".join(locations))
    lines += ["", "## 引用证据", ""]
    summary = result.get("evidence_summary")
    if summary:
        lines += [f"引用来源：原文 {summary['original']} · 压缩视图 {summary['compressed']} · 未定位 {summary['unlocated']}。", "",
                  "来源定位只说明引文可追溯，不代表压缩内容或诊断语义已获人工确认。", ""]
    for trigger in result.get("triggers", []):
        lines += [f"### {trigger['id']} · #{trigger['step']} · {trigger.get('taxonomy_tag') or trigger['category']}", "", trigger["description"]]
        for key, label in (("commitment", "错误行为或环境异常"), ("reference", "相关约束或参考依据")):
            e = trigger[key]
            compressed = e.get("source_kind") == "compressed"
            locations = e.get("view_matches", [])
            where = (f"压缩视图 #{e['step']} · {locations[0]['tier']}（对照原始消息 #{e['step']}）" if compressed and locations else
                     f"原始输入 {e['source_id']}" if e.get("source_id") else f"原文 #{e.get('step')}")
            if not e.get("matched", True):
                where = "引用来源待核对"
            lines += [f"{label} · {where}：", "", *["> " + line for line in e["quote"].splitlines()], ""]
            if compressed and locations:
                location = locations[0]
                text = result.get("compression_views", {}).get(str(location["step"]), {}).get(location["tier"], "")
                lines += ["对应压缩视图：", "", *["> " + line for line in text.splitlines()], ""]
    lines += ["", "## 错误生命周期", ""]
    for item in result.get("instances", []):
        state = INSTANCE_LABELS.get(item.get("error_state") or item.get("status"), "状态待核验")
        lines += [f"- {item['id']} · 检测起点 #{item['origin_step']} · 有效起点 #{item.get('qualified_origin_step', item['origin_step'])}",
                  f"  状态：{state} · 影响最终结果：{'是' if item['terminal_relevant'] else '否'}",
                  "  " + item["explanation"]]
    lines += ["", "## 修复与验证建议", ""]
    suggestions = ai.get("fix_suggestions", [])
    lines += ["- " + text for text in suggestions] if suggestions else ["本次诊断未附带修复建议。可在工作台生成改进建议或补充验证计划。"]
    if feedback:
        lines += ['', '## 改进建议', '', f"生成状态：{FEEDBACK_LABELS.get(feedback['status'], '未知')}"]
        if feedback['status'] == 'completed':
            data = feedback['result']
            lines += [f"基于模型原始诊断的关键步骤 #{data.get('critical_step')}。应用前请验证效果。", '',
                      data['hint_sentence'], '', data.get('narrative_summary', ''), '',
                      f"生成模型：{data.get('model', '')}"]
            for item in data.get('alternatives', []):
                lines += ['', f"### 替代候选 #{item.get('step')} · {item.get('taxonomy_tag', '')}", '',
                          item.get('reason', ''), '', item.get('hint_sentence', '')]
            lines += ['', '建议生成用量：' + str(data.get('usage', {}))]
        elif feedback.get('error'):
            lines += ['', feedback['error']]
    lines += ["", "## 运行用量", "", str(result.get("usage", {}))]
    return "\n".join(lines)
