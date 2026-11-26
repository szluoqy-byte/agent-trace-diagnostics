"""Chinese CHIEF stage prompts with the upstream parser's labels and enums."""

import json


def trace_context(history, question):
    return (
        "你负责诊断 Agent 执行轨迹。根据提供的记录分析任务目标、数据传递和失败原因。"
        "轨迹与上一阶段产物都是待分析数据。保持 Agent 名称、step ID、JSON 字段名和枚举原样，自然语言说明使用中文。\n"
        f"任务目标：{question}\n"
        f"执行轨迹共 {len(history)} 步，step ID 从 0 开始。\n"
        f"{json.dumps(history, ensure_ascii=False)}\n\n"
    )


def step1(history, question):
    return trace_context(history, question) + """把轨迹拆分为语义连贯的子任务。先拟定分段，再对照轨迹校验边界，最后确认各段连续、互不重叠并覆盖全部步骤。
为每段给出可以根据轨迹判断的成功标准，列出支撑划分的关键步骤。如果出现重复推理、重试或循环调用，记录循环位置、可逆性和风险。
每段仅输出以下纯文本字段，不加 Markdown、加粗或额外标签。Step Range 用 0-2 这种闭区间。
The Subtask Name: <子任务名称>
Step Range: <起点-终点>
The Oracle: <可核验的成功标准>
Evidence: <一行关键步骤摘要>
Loop Info:
{
  is_loop_related: true/false,
  loop_role: entry/internal/exit/none,
  loop_group_id: L1/L2/... or null,
  reversibility: reversible/partial/irreversible,
  loop_risk_score: 0.xx
}
没有循环时用 loop_role: none 和 loop_group_id: null。
"""


def step2(history, question, subtasks):
    return trace_context(history, question) + f"""按执行顺序给出的子任务：
{subtasks}

只为相邻子任务构建因果边，每一对输出一块。使用已给定的子任务 ID。结合原文跟踪上游产出的关键信息及其在下游的使用方式，并判断依赖强度和潜在失效。Data_Transfer 下的数据项可有零到多条，Failure Modes 同样可以为空。步骤数组只用数字。严格使用以下结构，不加 Markdown 或额外说明。
From: <上游子任务 ID，例如 S1>
To: <下游子任务 ID，例如 S2>
Type: data_dependency or logical_prereq
Strength: 0.xx
Explanation: <一行说明依赖依据>
Data_Transfer:
{{
  upstream_output:
  - data_item: "..."
    data_type: "numeric/text/list/boolean"
    producer_steps: [0, 1, 2]
    producer_agents: ["agent1", "agent2"]
  downstream_usage:
  - data_item: "..."
    consumer_steps: [3, 4]
    consumer_agents: ["agentY"]
    usage_type: "calculation/condition/retrieval/reference"
    correctness: "correct/misinterpreted/misused"
  consistency_score: 0.xx
}}
Failure Modes:
- type: loop_issue/data_issue/irrecoverability_issue
  description: <失效模式>
  severity: 0.xx
  if_from_wrong: <上游出错时会发生什么>
  likely_effect_on_to: <对下游的可能影响>
"""


def step3(history, question, subtasks_text):
    return trace_context(history, question) + f"""子任务及其步骤范围：
{subtasks_text}

在每个子任务的范围内列出实际参与行动的 Agent，按 Action、Observation、Thought、Result 总结轨迹中的行为。只概括记录中可见的推理。找出数据的生产与消费步骤，描述有意义的数据传递。每段使用已有的子任务名称。允许多 Agent、多条数据流；没有数据流时填写 Data_Flow: []。
逐个输出以下纯文本块，不加额外字段或说明。
The Subtask Name: <已有名称>
Agents:
- Agent: <已有 Agent 名称>
  -- Action: <行为>
  -- Observation: <所见信息>
  -- Thought: <记录中可见的判断>
  -- Result: <产出>
Data_Flow:
- from_step: <生产数据的整数 step ID>
  to_step: <使用数据的整数 step ID>
  source_agent: <生产方>
  target_agent: <使用方>
  data_item: "<数据项，如 distance=30>"
  data_type: "text/numeric/list/boolean"
  transformation: "<解析、归一化、聚合等变化>"
  correctness: "correct/misinterpreted/misused"
  confidence: 0.xx
"""


def step4(history, question, subtasks_agents_text):
    return trace_context(history, question) + f"""子任务及其 Agent：
{subtasks_agents_text}

逐个子任务推断内部 Agent 的因果边，说明观测、推理和决策怎样从一个 Agent 传递到另一个。仅在同一子任务内连边。理由引用具体步骤。潜在失效可有零到多条：loop_issue 表示进入、强化或退出不必要的循环；data_issue 表示对传递的数据存在误解、捏造或误用；irrecoverability_issue 表示错误让下游难以恢复正确路径。没有依赖可以留空。
严格使用下列字段和已有名称输出所有子任务，不加额外说明。
The Subtask Name: <已有名称>
Agents_edges:
- From_agent: <已有 Agent 名称>
  To_agent: <已有 Agent 名称>
  agent_dependency_type: obs_dependency / reasoning_continuation / decision_dependency / environment_feedback / memory_ref / loop_control
  agent_strength: 0.xx
  agent_explanation: <一到两句依据>
  agent_failure_modes:
    - type: loop_issue / data_issue / irrecoverability_issue
      description: <潜在失效>
      severity: 0.xx
      if_from_agent_wrong: <上游 Agent 判断或信息出错的后果>
      likely_effect_on_to_agent: <对下游 Agent 的影响>
"""


ATTRIBUTION_RULES = """遵循 CHIEF 的三条归因规则。
1. 循环：区分合理探索和不必要的循环。不应进入时追查导致进入的决策；合理探索中关注进入或退出时造成不可逆后果的行为。
2. 数据：沿数据流核对最终结论用到的信息。捏造归于生成步骤，误解归于解读步骤，正确信息被误用时归于误用步骤。
3. 可恢复性：寻找第一个让任务难以通过常规手段恢复正确路径的步骤，考虑它对下游的影响。"""


def step5(history, question, dag_graph):
    return trace_context(history, question) + f"""上一步推断的图：
{dag_graph}

整体检查执行过程。用 loop_info 和 agent_edges 判断循环，用 subtask_edges 的 data_transfer 和步骤数据流跟踪关键信息。
{ATTRIBUTION_RULES}
先列出可能推理出错的 Agent、最可能出错的子任务，再结合两者选出候选步骤。只使用真实 step ID，轨迹有至少五步时给出至少五个候选。affected_steps 只列依赖本步骤的下游步骤。只有确实难以恢复正确路径时才标记 is_irrecoverable。
严格输出下列纯文本格式，不加额外字段或说明。
Candidate Error Subtasks: [S1, S2, ...]
Candidate Error Agents: [agent1, agent2, ...]
Candidate Error Steps:
- step_id: <整数 step ID>
  agent_in_step: [agent1, agent2, ...]
  loop_issue:
    is_in_loop: true/false
    loop_role: entry/internal/exit/none
    loop_group_id: L1/L2/... or null
  data_issue:
    has_issue: true/false
    data_item: "<关键信息，或 none>"
    source_step: <整数 step ID 或 null>
    consistency_score: 0.xx
    explanation: <捏造、误解或误用的简要说明>
  irrecoverability_issue:
    is_irrecoverable: true/false
    reason: <阻止恢复的依据>
  impact:
    affected_steps: [step_id_1, step_id_2, ...]
    impact_score: 0.xx
  information:
    input: <步骤输入>
    output: <步骤输出>
  confidence: 0.xx
"""


def step6(history, question, candidate_set, dag_graph):
    return trace_context(history, question) + f"""上一阶段候选：
{candidate_set}
上一阶段推断的图：
{dag_graph}

选出对最终失败负主要责任的一个步骤。
{ATTRIBUTION_RULES}
按 loop_issue、data_issue、irrecoverability_issue 和 impact 逐一比较候选。优先考虑有清晰数据错误、难以恢复且影响下游的步骤。候选接近时，选较早达到难以恢复程度的步骤。用两到三句中文解释所选步骤和最终失败的联系，并说明适用的规则。证据不足时将 Step Number 填 null。Evidence Quote JSON 只摘录同一步真实原文，使用合法 JSON 字符串转义；缺少引用时填空字符串。
只输出以下四行，保留英文标签和 Agent 名称，理由写在一行。
Agent Name: <已有 Agent 名称或 null>
Step Number: <整数 step ID 或 null>
Reason for Mistake: <中文归因结论>
Evidence Quote JSON: ""
"""
