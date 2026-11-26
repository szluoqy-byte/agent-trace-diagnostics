"""Human-readable export; the raw model output is exported separately unchanged."""

from .diagnosis.synthesis import LEGACY_COMPARISON_ENGINES, SUPPORT_LABELS, project_synthesis, review_target
from .diagnosis.review_policy import AGREEMENT_STATES, review_status

REVIEW_LABELS = {"confirmed": "已确认", "modified": "已修改", "rejected": "已驳回", "waived": "免复核"}
FEEDBACK_LABELS = {"queued": "排队中", "running": "生成中", "completed": "已生成", "failed": "生成失败"}
ENGINE_LABELS = {"trajdebug": "TrajDebug", "agentdebugx": "AgentDebugX", "chief": "CHIEF"}
COMPARISON_LABELS = {
    "aligned": "同一步骤，各路均有原文引用",
    "same_step": "同一步骤，原文引用有缺口",
    "different": "关键步骤不同",
    "unresolved": "至少一路未定位",
    "incomplete": "部分引擎未完成",
    "succeeded": "业务成功",
}
INSTANCE_LABELS = {
    "manifest_active": "持续影响最终结果",
    "latent_active": "未修复",
    "clean_resolution": "已修复",
    "costly_resolution": "已修复，仍有资源代价",
    "exploration": "探索行为，已排除",
    "unknown": "状态待核验",
}


def markdown_report(trace, job, reviews, feedback=None):
    result = project_synthesis(job["result"])
    ai = result["attribution"]
    final = reviews[0]["data"] if reviews else review_target(result)[0]
    step = final.get("critical_step")
    comparison = result.get("comparison") or {}
    runs = result.get("engine_runs") or {}
    lines = [f"# 天罗智能轨迹诊断 · {trace['title']}", ""]
    if result.get("mode") == "demo":
        lines += ["历史合成数据，未运行模型。", ""]
    else:
        lines += [f"诊断模型：{result.get('model') or '未记录'}", ""]
        if result.get("algorithm") == "CHIEF":
            lines += ["诊断引擎：CHIEF", ""]
            if agent := result.get("root_cause_agent"):
                lines += [f"根因 Agent：{agent}", ""]
    if (synthesis := result.get("synthesis", {})).get("status") == "completed":
        lines += ["## 综合分析", "", synthesis["summary"], ""]
        preferred_step = synthesis.get("critical_step")
        if preferred_step is not None:
            label = ("一致关键步骤" if comparison.get("state") in AGREEMENT_STATES
                     else "模型候选步骤" if reviews else "暂定关键步骤（待人工复核）")
            lines += [f"{label} #{preferred_step}", ""]
        else:
            lines += ["关键失败步骤待人工判断。", ""]
        for engine, assessment in (synthesis.get("assessments") or {}).items():
            branch = result if engine == comparison.get("primary") else (runs.get(engine, {}).get("report") or {})
            candidate = (branch.get("attribution") or {}).get("critical_step")
            location = f"#{candidate}" if candidate is not None else "未定位"
            label = ENGINE_LABELS.get(engine, engine)
            lines += [f"- {label} · {location} · 候选置信度 {SUPPORT_LABELS[assessment['support']]} · {assessment.get('reason', '')}"]
        lines += evidence_lines(synthesis) + [""]
    status = review_status(comparison.get("state"), final.get("decision"))
    lines += ["## 当前结论", "", final["summary"],
              f"关键步骤：{f'#{step}' if step is not None else '未确定'} | 根因类别：{final['category']}",
              f"人工复核：{REVIEW_LABELS.get(status, '待复核')}", final.get("notes", ""),
              "", "## 模型原始结论", "", ai["reasoning"]]
    for warning in result.get("warnings", []):
        lines += ["", "提示：" + warning]
    if comparison:
        lines += ["", "## 多路诊断对照" if len(runs) > 2 else "## 双路诊断对照", "", COMPARISON_LABELS[comparison["state"]], ""]
        participants = comparison.get("engines", LEGACY_COMPARISON_ENGINES)
        for name, run in runs.items():
            label = ENGINE_LABELS.get(name, name)
            if run["status"] == "completed" and name not in participants:
                label += "（参考）"
            branch = result if name == comparison["primary"] else (run.get("report") or {})
            attribution = branch.get("attribution") or {}
            branch_step = attribution.get("critical_step")
            title = f"#{branch_step}" if branch_step is not None else "未定位"
            lines.append(f"- {label} · {title} · {attribution.get('summary') or run.get('error', '')}")
            if name in {"agentdebugx", "chief"} and branch is not result:
                lines.extend(evidence_lines(branch))
                lines.extend("  - 建议 " + item for item in attribution.get("fix_suggestions", []))
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
    lines.extend(evidence_lines(result))
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
    if result.get("algorithm") != "CHIEF":
        lines += ["", "## 错误生命周期", ""]
    for item in result.get("instances", []):
        state = INSTANCE_LABELS.get(item.get("error_state") or item.get("status"), "状态待核验")
        lines += [f"- {item['id']} · 检测起点 #{item['origin_step']} · 有效起点 #{item.get('qualified_origin_step', item['origin_step'])}",
                  f"  状态：{state} · 影响最终结果：{'是' if item['terminal_relevant'] else '否'}",
                  "  " + item["explanation"]]
    if result.get("algorithm") != "CHIEF":
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


def evidence_lines(report):
    lines = []
    for item in report.get("evidence", []):
        location = f"原文 #{item.get('step')}" if item.get("matched") else "未定位"
        lines.append(f"  - {location} · {item['quote']}")
    return lines
