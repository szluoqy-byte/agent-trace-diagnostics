"""Chinese templates for the vendored DeepDebug text diagnosis profile."""

from string import Template


ZH_PROFILE = "deepdebug-zh-v1"
EN_PROFILE = "deepdebug-upstream-en"

# The numbered steps, event references, option values, taxonomy keys and quoted
# trace fragments belong to the upstream contract. Interpolation inserts them
# verbatim, including punctuation and any literal dollar signs.
ZH_TEMPLATES = {
    "global.system": """
你负责分析 Agent 为解决实际问题执行的轨迹。判断哪一个步骤的错误直接造成了最终失败。

仅返回符合下列结构的 JSON 对象，不要附加说明或 Markdown。

{
  "span_id": "<输入中的 event_id，或 null>",
  "step_index": <整数或 null>,
  "agent_name": "<输入中的 agent_name，或 null>",
  "confidence": <0 到 1 之间的小数>,
  "rationale": "<用一到两句话说明判断依据>",
  "evidence": ["<原文中的简短引文>", ...]
}

如果轨迹看起来没有失败，将各字段设为 null，confidence 设为 0。""",
    "global.user": """
分析 Agent 解决实际问题的会话。
任务目标：$goal
$semantics$ground_truth$reference请指出哪个 Agent 在哪一步犯了决定性错误，并解释它为何直接导致失败。

会话轨迹：

$events
""",
    "cascade_half.system": """
一次 Agent 运行失败。轨迹片段分成前半段和后半段。判断哪一半包含最早的根因步骤。
根因步骤自身的输出应构成决定性错误。区分根因与错误显现、执行器报告失败或下游继承前序错误。
前半段已经出现真正的错误决策时，选择 upper。
仅返回 JSON：{"half":"upper" | "lower","confidence":<小数>}""",
    "cascade_half.user": """
任务目标：$goal
$ground_truth前半段（步骤 $first_start 至 $first_end）：
$upper

后半段（步骤 $second_start 至 $second_end）：
$lower

哪一半包含最早的根因错误？""",
    "cascade_pick.system": """
Agent 运行失败。找出最早且其自身输出已足以导致失败的根因步骤。
错误显现、工具返回失败和继承前序错误都属于可能的后续表现。请指出负责该决策的 Agent。
仅返回 JSON：{"event_id":"<事件 ID>","agent":"<Agent>","step":<整数>,"confidence":<0 到 1 之间的小数>}""",
    "cascade_pick.user": """
任务目标：$goal
$ground_truth$reference$label_hint
$labels候选步骤：$steps

$events

哪个步骤构成决定性根因？该步骤由哪个组件负责？""",
    "bisect_half.system": """
一次 Agent 运行失败。轨迹片段分为前半段和后半段。判断哪一半包含对失败影响最大的错误步骤。
仅返回 JSON：{"half":"upper" | "lower","confidence":<小数>}""",
    "bisect_half.user": """
任务目标：$goal
$ground_truth前半段（步骤 $first_start 至 $first_end）：
$upper

后半段（步骤 $second_start 至 $second_end）：
$lower

哪一半包含决定性错误步骤？""",
    "bisect_pick.system": """
一次 Agent 运行失败。结合完整会话，从候选步骤中只选一个对失败最关键的步骤。
优先选择纠正后最可能避免失败的步骤，并指出负责该步骤的组件。
仅返回 JSON：{"event_id":"<事件 ID>","step":<候选中的整数>,"agent":"<Agent>","confidence":<0 到 1 之间的小数>}""",
    "bisect_pick.user": """
任务目标：$goal
$ground_truth$reference$label_hint
$labels从候选步骤中选出一个：$steps

$events

哪个候选是决定性错误步骤？""",
    "arbitration.system": """
一次 Agent 运行失败。下面是两个候选事件及其上下文。
选择一个决定性错误事件，判断纠正哪个事件最可能避免失败。
仅返回 JSON：{"candidate":"A" | "B","event_id":"<事件 ID>"}""",
    "arbitration.user": """
任务目标：$goal
$ground_truth候选 A：事件 $id_a，步骤 $step_a，Agent $agent_a。
$context_a

候选 B：事件 $id_b，步骤 $step_b，Agent $agent_b。
$context_b

哪个候选事件是决定性根因？""",
    "refine.system": """
你负责撰写 AgentDebugX-DeepDebug 的最终诊断。决定性根因步骤已经确定，请沿用该步骤。
结合该事件及附近步骤，用一到两句话解释它为何导致失败，引用支持判断的简短原文，并给出一条可操作的修复建议。
每条证据必须逐字引用给出的事件内容，并带上对应的 event_id。保留工具名、API、JSON 字段和引文原文。
仅返回 JSON：{"summary":"<中文诊断结论>","evidence":[{"event_id":"<事件 ID>","quote":"<逐字引文>"}],"suggestion":"<一条中文修复建议>"}""",
    "refine.user": """
任务目标：$goal
$ground_truth$reference已定位的根因是事件 $event_id，步骤 $step，Agent $agent。

上下文：
$events

请撰写步骤 $step 的诊断。""",
    "common.goal_unknown": "目标未知",
    "common.empty_events": "空会话",
    "common.ground_truth": "任务参考答案：$answer\n",
    "common.candidate_labels": "可选组件：$labels\n",
    "global.semantics.all_events": "步骤口径为 all_events，从 0 开始。每个可归因事件算一个步骤。\n",
    "global.semantics.env_agent_pairs": "步骤口径为 env_agent_pairs，从 1 开始。每步由环境观测和随后的 Agent 响应组成。\n",
    "refine.fallback_summary": "Agent $agent 在步骤 $step 的决策引入了导致最终失败的最早错误。",
    "refine.fallback_constraint": "执行该决策前核对所选方案是否满足 $constraint 约束。无法确认满足时停止执行。",
    "refine.fallback_suggestion": "按任务的原始约束重新检查该决策，并在调用工具前验证纠正后的操作。",
    "refine.prior_evidence": " 证据：$evidence",
    "refine.prior_signals": "前置检测或裁决提供的线索可能有误。采信前请核对轨迹。错误显现的步骤常位于根因之后。\n$entries",
}


def render_diagnostic_prompt(language: str, key: str, default: str, values: dict) -> str:
    if language != "zh":
        return default
    return Template(ZH_TEMPLATES[key].removeprefix("\n")).substitute(values)


def prompt_profile(language: str) -> str:
    return ZH_PROFILE if language == "zh" else EN_PROFILE
