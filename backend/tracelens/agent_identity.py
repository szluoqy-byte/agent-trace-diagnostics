"""Resolve a trajectory's Agent identity from recorded span attributes and links."""

from dataclasses import dataclass
import json

from .models import AgentIdentity


def text(value):
    return value.strip() if isinstance(value, str) else ''


def attributes(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {item['key']: item['value'].get('stringValue') for item in value
                if isinstance(item, dict) and isinstance(item.get('key'), str) and isinstance(item.get('value'), dict)}
    return {}


@dataclass(frozen=True)
class AgentSpan:
    id: str
    parent: str
    invocation: bool
    identity: AgentIdentity | None


def deepagent_span(span):
    attrs = attributes(span.get('attributes'))
    kind = (text(span.get('ai_span_kind')) or text(attrs.get('gen_ai.span.kind'))).upper()
    name = text(span.get('service_name'))
    span_id = span['span_id']
    output = span.get('span_output')
    identity = None
    if name and kind in {'AGENT', 'LLM'} and output and (not isinstance(output, str) or output.strip()):
        agent_id = text(attrs.get('gen_ai.agent.id'))
        key = json.dumps(['deepagent', text(span.get('name_space')), name, agent_id], ensure_ascii=False)
        identity = AgentIdentity(name=name, key=key, agent_id=agent_id or None, span_id=span_id, field='service_name')
    return AgentSpan(span_id, text(span.get('parent_id')), kind == 'AGENT', identity)


def otel_span(record):
    span = record['span']
    span_id = span['spanId'].lower()
    attrs = attributes(span.get('attributes'))
    operation = attrs.get('gen_ai.operation.name')
    agent_id = text(attrs.get('gen_ai.agent.id'))
    name = text(attrs.get('gen_ai.agent.name'))
    identity = None
    if operation not in {'execute_tool', 'create_agent', 'invoke_workflow'} and (name or agent_id):
        resource = attributes(record.get('resource', {}).get('attributes'))
        key = (['otel_genai', 'id', text(attrs.get('gen_ai.provider.name')), agent_id] if agent_id else
               ['otel_genai', 'name', text(resource.get('service.namespace')), name])
        identity = AgentIdentity(name=name or agent_id, key=json.dumps(key, ensure_ascii=False),
                                 agent_id=agent_id or None, span_id=span_id,
                                 field='gen_ai.agent.name' if name else 'gen_ai.agent.id')
    return AgentSpan(span_id, text(span.get('parentSpanId')).lower(), operation == 'invoke_agent', identity)


class AgentOwnership:
    def __init__(self, data):
        self.deepagent = bool(data.get('deepagent'))
        if self.deepagent:
            spans = map(deepagent_span, data['deepagent'].get('spans', []))
        else:
            spans = map(otel_span, (data.get('otel') or {}).get('spans', []))
        self.spans = {span.id: span for span in spans}

    def ancestry(self, span_id):
        seen = set()
        while (span := self.spans.get(span_id)) and span_id not in seen:
            yield span
            seen.add(span_id)
            span_id = span.parent

    def entry(self):
        invocations = [span for span in self.spans.values() if span.invocation]
        outer = [span for span in invocations if not any(parent.invocation for parent in self.ancestry(span.parent))]
        if outer and all(span.identity for span in outer):
            return self.unique(outer)
        # Capture formats may only instrument the model call. A unique recorded
        # identity is usable if there is no competing entry invocation.
        return self.unique(self.spans.values()) if len(outer) <= 1 else None

    @staticmethod
    def unique(spans):
        identities = {span.identity.key: span.identity for span in spans if span.identity}
        return next(iter(identities.values())) if len(identities) == 1 else None
