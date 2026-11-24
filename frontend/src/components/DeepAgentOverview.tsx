import {useMemo, useState} from 'react'
import type {Trace} from '../types'
import {useResource} from '../workspace'

interface Span {
  span_id: string
  [key: string]: unknown
}

interface DeepAgentSource {
  spans: Span[]
  message_sources: {step: number; span_id: string; field: string}[]
}

function SpanRecord({span, steps}: {span: Span; steps?: number[]}) {
  const [open, setOpen] = useState(false)
  const name = [span.span_display_name, span.span_name, span.ai_span_kind]
    .find((value): value is string => typeof value === 'string' && !!value) ?? 'Span'
  return <details className="otel-message-source" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><strong>{name}</strong><code>{span.span_id}</code>
      {steps?.length ? <span>诊断步骤 {steps.map(step => `#${step}`).join('、')}</span> : null}
    </summary>
    {open ? <pre aria-label={`原始 span ${span.span_id}`}>{JSON.stringify(span, null, 2)}</pre> : null}
  </details>
}

function SpanList({path}: {path: string}) {
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(20)
  const [version, setVersion] = useState(0)
  const {data, error} = useResource<DeepAgentSource>(path, version)
  const sourceSteps = useMemo(() => {
    const steps = new Map<string, number[]>()
    for (const source of data?.message_sources ?? []) {
      const indexes = steps.get(source.span_id) ?? []
      indexes.push(source.step)
      steps.set(source.span_id, indexes)
    }
    return steps
  }, [data])
  if (!data) return error
    ? <p role="alert">{error} <button className="text-button" onClick={() => setVersion(value => value + 1)}>重试</button></p>
    : <p role="status">正在读取原始 span…</p>

  const term = query.trim().toLowerCase()
  const visible = data.spans.flatMap((span, index) => !term ||
    [span.span_id, span.ai_span_kind, span.span_display_name, span.span_name].some(value => typeof value === 'string' && value.toLowerCase().includes(term))
    ? [{span, index}] : [])
  return <>
    <label className="deepagent-span-search">筛选原始 span
      <input type="search" value={query} placeholder="名称、类型或 Span ID" onChange={event => {setQuery(event.target.value); setLimit(20)}}/>
    </label>
    {visible.slice(0, limit).map(({span, index}) => <SpanRecord key={index} span={span} steps={sourceSteps.get(span.span_id)}/>)}
    {!visible.length ? <p role="status">没有匹配的 span</p> : null}
    {visible.length > limit ? <button className="text-button" onClick={() => setLimit(value => value + 20)}>显示更多 span（已显示 {limit} / {visible.length}）</button> : null}
  </>
}

export default function DeepAgentOverview({trace}: {trace: Trace}) {
  const [loaded, setLoaded] = useState(false)
  if (trace.data.input_format !== 'deepagent') return null
  const source = trace.data.deepagent
  const path = `/traces/${trace.id}/deepagent`
  return <aside className="otel-overview deepagent-overview" aria-label="DeepAgent 原始采集记录">
    <div><strong>DeepAgent</strong>
      {source ? <><span>{source.span_count} 个原始 span · {trace.data.messages.length} 条诊断消息</span>
        <a href={`/api${path}?download=true`} download>下载原始 span 和步骤映射</a></> : null}
    </div>
    {source ? <details onToggle={event => {if (event.currentTarget.open) setLoaded(true)}}>
      <summary>查看全部原始 span</summary>
      {loaded ? <SpanList path={path}/> : null}
    </details> : <p>在轨迹库重新导入原始文件，可为这条历史轨迹补齐 span。</p>}
  </aside>
}
