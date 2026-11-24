from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OTelSource(Contract):
    trace_id: str
    span_id: str
    attribute: str
    message_index: int | None = None


class OTelProvenance(Contract):
    trace_id: str
    adapter_version: str = "otel-genai-v1"
    spans: list[dict] = Field(max_length=20000)
    warnings: list[str] = Field(default_factory=list)


class DeepAgentProvenance(Contract):
    trace_id: str
    spans: list[dict] = Field(min_length=1)
    export_metadata: dict


class Message(Contract):
    step: int = Field(ge=0)
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str = ""
    source_step: str = ""
    judgable: bool | None = None
    compress_role: Literal["compress", "preserve", "default"] = "default"
    source_refs: list[OTelSource] = Field(default_factory=list)


class RuntimeInput(Contract):
    """Explicit importer attestation, never inferred from arbitrary metadata."""
    source_id: str = Field(min_length=1)
    kind: Literal["task", "policy", "tool_schema"]
    content: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    available_from: int = Field(ge=0)
    visibility: Literal["agent_visible"]


class Trajectory(Contract):
    title: str
    task_id: str
    dataset: str = "custom"
    task_description: str = ""
    outcome: Literal["failed", "succeeded", "unknown"] = "unknown"
    messages: list[Message] = Field(min_length=1, max_length=20000)
    context: str = ""
    source: str = ""
    annotation: dict = Field(default_factory=dict)
    kind: Literal["imported", "demo"] = "imported"
    runtime_inputs: list[RuntimeInput] = Field(default_factory=list)
    agent_framework_description: str = ""
    otel: OTelProvenance | None = None
    input_format: Literal["deepagent"] | None = None
    deepagent: DeepAgentProvenance | None = None

    @model_validator(mode="after")
    def indices(self):
        if any(m.step != i for i, m in enumerate(self.messages)):
            raise ValueError("step 必须是从 0 开始的连续消息索引")
        if len({s.source_id for s in self.runtime_inputs}) != len(self.runtime_inputs):
            raise ValueError("runtime_inputs.source_id 必须唯一")
        if any(s.available_from >= len(self.messages) for s in self.runtime_inputs):
            raise ValueError("运行时输入的可见时间超出轨迹范围")
        return self

    def input_json(self, *, fingerprint=False):
        # Preserve the hashes of existing traces/reports when no new sources exist.
        excluded = {"annotation", "otel", "input_format", "deepagent"}
        if fingerprint:
            excluded |= {"title", "source"}
        if not self.runtime_inputs:
            excluded.add("runtime_inputs")
        if not self.agent_framework_description:
            excluded.add("agent_framework_description")
        excluded_fields = {key: True for key in excluded}
        message_fields = {}
        for i, message in enumerate(self.messages):
            fields = {"source_refs"}
            if message.judgable is None:
                fields.add("judgable")
            if message.compress_role == "default":
                fields.add("compress_role")
            if fields:
                message_fields[i] = fields
        if message_fields:
            excluded_fields["messages"] = message_fields
        return self.model_dump_json(exclude=excluded_fields)


def trajectory_format(data: dict) -> str:
    return data.get("input_format") or ("otel_genai" if data.get("otel") else "messages")


class Review(Contract):
    decision: Literal["confirmed", "modified", "rejected"]
    critical_step: int | None = Field(default=None, ge=0)
    category: str = Field(max_length=200)
    summary: str = Field(min_length=1, max_length=5000)
    notes: str = Field(default="", max_length=5000)

