import type { Trace } from '../types'

export default function OTelOverview({trace}: {trace:Trace | null}) {
  const otel = trace?.data.otel
  if (!otel || !trace) return null
  return <aside className="otel-overview" aria-label="OTel 导入信息"><div><strong>OTel GenAI</strong><span>{otel.spans.length} 个 span → {trace.data.messages.length} 条消息</span><a href={`/api/traces/${trace.id}/otel`} download>下载原始 span 和消息映射</a></div><p>Trace ID：<code>{otel.trace_id}</code> · 展开消息下方的「OTel 来源」可查看该消息对应的采集记录。</p>{otel.warnings.map((warning, i) => <p key={i} className="otel-warning">{warning}</p>)}</aside>
}
