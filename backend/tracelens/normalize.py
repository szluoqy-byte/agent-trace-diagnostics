import hashlib
import json
from codecs import BOM_UTF16_BE, BOM_UTF16_LE
from .deepagent import convert_deepagent, is_deepagent
from .models import Message, Trajectory
from .otel_genai import convert_envelopes, is_otlp


def normalize(data: dict, filename: str = "trajectory.json") -> Trajectory:
    if not isinstance(data, dict):
        raise ValueError("每条轨迹必须是 JSON 对象")
    if "messages" in data and "metadata" in data:
        meta = data["metadata"]
        raw = data["messages"]
        if not isinstance(meta, dict):
            raise ValueError("metadata 必须是对象")
        if not isinstance(raw, list) or not raw:
            raise ValueError("messages 必须是非空数组")
        if any(not isinstance(m, dict) or type(m.get("step")) is not int or m["step"] != i for i, m in enumerate(raw)):
            raise ValueError("messages 中的 step 必须从 0 开始，并按消息顺序连续编号")
        dataset = meta.get("dataset", "trajdebug")
        task_id = str(meta.get("task_id", filename))
        task = meta.get("task_description", "")
        annotation = meta.get("annotation") or {}
        reward = meta.get("reward")
    else:
        raw = next((data[k] for k in ["messages", "traj", "events", "trajectory"] if k in data), None)
        meta = data.get("metadata", {})
        dataset = data.get("dataset", "agentrx-tau" if "traj" in data else "custom")
        task_id = str(data.get("task_id", data.get("trajectory_id", filename)))
        # AgentRx info.task.instruction describes hidden user simulation; never use it.
        task = data.get("task_description", data.get("instruction", ""))
        annotation = data.get("annotation", {})
        reward = data.get("reward")
        if reward is None and data.get("outcome") in {"failed", "succeeded"}:
            reward = 0 if data["outcome"] == "failed" else 1
    if not isinstance(raw, list) or not raw:
        raise ValueError("缺少非空 messages / traj / events 消息数组")
    messages = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            raise ValueError(f"消息 #{i} 必须是对象")
        role = m.get("role", m.get("source", ""))
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"消息 #{i} 的 role 不合法：{role}")
        content = m.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if m.get("tool_calls"):
            content += ("\n" if content else "") + json.dumps(m["tool_calls"], ensure_ascii=False, indent=2)
        messages.append(Message(step=i, role=role, content=content, name=str(m.get("name") or ""), source_step=str(m.get("step", m.get("index", i))),
                                judgable=m.get("judgable"), compress_role=m.get("compress_role", "default")))
    if not task:
        task = next((m.content for m in messages if m.role == "user"), "")
    # Allow only explicit domain context. Never pass arbitrary metadata to the model.
    context = data.get("context", "")
    if not isinstance(context, str):
        context = json.dumps(context, ensure_ascii=False)
    title = data.get("title") or filename.rsplit(".", 1)[0]
    extra = meta.get("extra") if isinstance(meta.get("extra"), dict) else {}
    return Trajectory(title=title, task_id=task_id, dataset=str(dataset), task_description=task,
                      outcome="failed" if reward == 0 else "succeeded" if reward == 1 else "unknown",
                      messages=messages, context=context, annotation=annotation,
                      source=str(data.get("source", filename)), runtime_inputs=data.get("runtime_inputs", []),
                      agent_framework_description=str(extra.get("agent_framework_description", "")))


WORKBENCH_FORMAT_ERROR = "工作台仅支持 DeepAgent 轨迹 JSON 或 OTel GenAI 轨迹文件"


def parse_upload_item(data, filename: str, *, workbench=False) -> list[Trajectory]:
    if is_deepagent(data):
        return convert_deepagent(data, filename)
    if workbench:
        raise ValueError(WORKBENCH_FORMAT_ERROR)
    return [normalize(data, filename)]


def parse_upload(content: bytes, filename: str, *, workbench=False) -> list[Trajectory]:
    encoding = "utf-16" if content.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else "utf-8-sig"
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("轨迹文件请保存为 UTF-8 或带 BOM 的 UTF-16 编码") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as e:
            raise ValueError(f"无效 JSON / JSONL：第 {e.lineno} 行") from e
    if is_otlp(data):
        return convert_envelopes([data], filename)
    if isinstance(data, list):
        if data and all(isinstance(x, dict) and "role" in x for x in data):
            data = [{"messages": data}]
        envelopes = [item for item in data if is_otlp(item)]
        others = [item for item in data if not is_otlp(item)] if envelopes else data
        traces = convert_envelopes(envelopes, filename) if envelopes else []
        for index, item in enumerate(others, 1):
            name = filename if len(data) == 1 else f"{filename}:{index}"
            traces.extend(parse_upload_item(item, name, workbench=workbench))
        return traces
    return parse_upload_item(data, filename, workbench=workbench)


def fingerprint(trace: Trajectory) -> str:
    return hashlib.sha256(trace.input_json(fingerprint=True).encode()).hexdigest()

