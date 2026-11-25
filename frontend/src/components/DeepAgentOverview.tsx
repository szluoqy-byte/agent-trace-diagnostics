import {useEffect, useId, useMemo, useRef, useState} from 'react'
import type {Evidence, Message, Trace} from '../types'
import {useResource} from '../workspace'
import DeepAgentTimeline, {durationLabel} from './DeepAgentTimeline'
import Tabs from './Tabs'
import TraceViewer from './TraceViewer'

interface Span {
  span_id: string
  parent_id?: string | null
  span_input?: unknown
  span_output?: unknown
  [key: string]: unknown
}

interface SpanNode {
  span_id: string
  title: string
  kind: string
  duration_us: number | null
  start_offset_us?: number | null
  end_offset_us?: number | null
  depth?: number
  status_code: string | number | null
  finish_reason: string | null
}

interface DeepAgentSource {
  spans: Span[]
  nodes?: SpanNode[]
  message_sources: {step: number; span_id: string; field: string}[]
}

const callTabs = [{id:'timeline', label:'调用时序'}, {id:'calls', label:'调用列表'}]

function capturedText(raw: unknown): string | null {
  let value = raw
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw)
    } catch {
      return raw
    }
  }
  if (value == null) return null
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function CapturedField({label, value}: {label: string; value: unknown}) {
  const text = capturedText(value)
  return <section>
    <h3>{label}</h3>
    {text ? <pre aria-label={label}>{text}</pre> : <p className="muted">{text === null ? '未采集' : '空内容'}</p>}
  </section>
}

function EvidenceText({message, selection}: {message: Message; selection: Evidence | null}) {
  const content = message.content
  const quote = selection?.step === message.step ? selection.quote : ''
  const offset = selection?.start != null && quote ? Array.from(content).slice(0, selection.start).join('').length : -1
  const index = quote ? (offset >= 0 && content.startsWith(quote, offset) ? offset : content.indexOf(quote)) : -1
  return <pre aria-label={`诊断步骤 #${message.step}`}>
    {index < 0 ? content : <>{content.slice(0, index)}<mark>{content.slice(index, index + quote.length)}</mark>{content.slice(index + quote.length)}</>}
  </pre>
}

function SpanRecord({span, node, parentTitle, steps, critical, focused, selection}: {
  span: Span; node: SpanNode & {number: number}; parentTitle?: string; steps: Message[]; critical: boolean;
  focused: boolean; selection: Evidence | null
}) {
  const record = useRef<HTMLDetailsElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const element = record.current
    if ((!selection && !focused) || !element) return
    element.open = true
    setOpen(true)
    element.querySelector('summary')?.focus({preventScroll:true})
    element.scrollIntoView?.({block:'nearest'})
  }, [focused, selection])
  return <details ref={record} className={`deepagent-call${critical ? ' deepagent-critical' : ''}`}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <strong>#{node.number} {node.title}</strong>
      <span className="deepagent-kind">{node.kind}</span>
      <span className="deepagent-duration">{durationLabel(node.duration_us)}</span>
      {critical ? <span className="critical-label">关键调用</span> : null}
    </summary>
    {open ? <div className="deepagent-call-body">
      <div className="deepagent-call-meta">
        <span>Span ID <code>{span.span_id}</code></span>
        {span.parent_id ? <span>父调用 {parentTitle || span.parent_id}</span> : null}
        {node.status_code != null && node.status_code !== '' ? <span>Span 状态码 <code>{node.status_code}</code></span> : null}
        {node.finish_reason ? <span>结束原因 <code>{node.finish_reason}</code></span> : null}
      </div>
      <div className="deepagent-call-io">
        <CapturedField label="输入" value={span.span_input}/>
        <CapturedField label="输出" value={span.span_output}/>
      </div>
      {steps.length ? <details open={selection ? true : undefined}>
        <summary>诊断内容</summary>
        {steps.map(message => <div key={message.step} className="deepagent-diagnostic-step">
          <small className="muted">算法步骤 #{message.step}</small>
          <EvidenceText message={message} selection={selection}/>
        </div>)}
      </details> : null}
      <details>
        <summary>原始 span</summary>
        <pre aria-label={`原始 span ${span.span_id}`}>{JSON.stringify(span, null, 2)}</pre>
      </details>
    </div> : null}
  </details>
}

export default function DeepAgentOverview({trace, criticalStep, selection}: {
  trace: Trace; criticalStep: number | null; selection: Evidence | null
}) {
  const path = `/traces/${trace.id}/deepagent`
  const tabsId = useId()
  const [tab, setTab] = useState(callTabs[0].id)
  const [focusedSpan, setFocusedSpan] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(20)
  const [version, setVersion] = useState(0)
  const {data, error} = useResource<DeepAgentSource>(path, version)
  const {byId, numberedNodes, nodesById, stepsBySpan, spanByStep, totalUs} = useMemo(() => {
    const byId = new Map(data?.spans.map(span => [span.span_id, span]))
    const numberedNodes = (data?.nodes ?? []).map((node, index) => {
      const name = byId.get(node.span_id)?.span_name
      return {...node, number: index + 1, title: typeof name === 'string' && name.trim() ? name.trim() : node.title}
    })
    const nodesById = new Map(numberedNodes.map(node => [node.span_id, node]))
    const stepsBySpan = new Map<string, Message[]>()
    const spanByStep = new Map<number, string>()
    for (const source of data?.message_sources ?? []) {
      const message = trace.data.messages[source.step]
      if (!message) continue
      const steps = stepsBySpan.get(source.span_id) ?? []
      steps.push(message)
      stepsBySpan.set(source.span_id, steps)
      spanByStep.set(source.step, source.span_id)
    }
    const totalUs = numberedNodes.reduce<number | null>((total, node) => node.end_offset_us == null
      ? total
      : Math.max(total ?? 0, node.end_offset_us), null)
    return {byId, numberedNodes, nodesById, stepsBySpan, spanByStep, totalUs}
  }, [data, trace.data.messages])
  const selectedSpan = selection?.step != null ? spanByStep.get(selection.step) : undefined
  const criticalSpan = criticalStep != null ? spanByStep.get(criticalStep) : undefined
  useEffect(() => {
    if (!selectedSpan || !data?.nodes) return
    const index = data.nodes.findIndex(node => node.span_id === selectedSpan)
    setTab('calls')
    setFocusedSpan(null)
    setQuery('')
    setLimit(previous => Math.max(previous, index + 1))
  }, [selectedSpan, selection, data])

  if (data && !data.nodes) return <TraceViewer messages={trace.data.messages} criticalStep={criticalStep} selection={selection}/>
  const term = query.trim().toLowerCase()
  const visible = numberedNodes.filter(node => !term || [node.title, node.kind, node.span_id].some(value => value.toLowerCase().includes(term)))
  const current = visible.slice(0, limit)
  return <section className="panel trace-panel deepagent-panel" aria-label="DeepAgent 执行轨迹">
    <div className="panel-heading">
      <h2>执行轨迹 <span className="count">{trace.data.deepagent?.span_count ?? 0} 个调用</span></h2>
      <a className="text-button" href={`/api${path}?download=true`} download>下载原始 span 和步骤映射</a>
    </div>
    <Tabs id={tabsId} label="执行轨迹视图" className="ws-tabs deepagent-tabs"
      items={callTabs} active={tab} onChange={setTab}/>
    <label className="deepagent-span-search">筛选调用
      <input type="search" value={query} placeholder="名称、类型或 Span ID" onChange={event => {setQuery(event.target.value); setLimit(20)}}/>
    </label>
    <div className="messages deepagent-calls" role="tabpanel" id={`${tabsId}-panel-${tab}`} aria-labelledby={`${tabsId}-tab-${tab}`}>
      {error ? <p role="alert">{error} <button className="text-button" onClick={() => setVersion(value => value + 1)}>重试</button></p> : null}
      {!data && !error ? <p role="status">正在读取调用…</p> : null}
      {tab === 'timeline' && current.length ? (totalUs == null
        ? <p role="status">这条轨迹缺少可用于绘图的调用时间。</p>
        : <DeepAgentTimeline calls={current} totalUs={totalUs} criticalSpan={criticalSpan} onOpen={spanId => {
          setFocusedSpan(spanId)
          setTab('calls')
        }}/>) : null}
      {tab === 'calls' ? current.map(node => {
        const span = byId.get(node.span_id)
        if (!span) return null
        return <SpanRecord key={node.span_id} span={span} node={node}
          parentTitle={span.parent_id ? nodesById.get(span.parent_id)?.title : undefined}
          steps={stepsBySpan.get(node.span_id) ?? []} critical={criticalSpan === node.span_id}
          focused={focusedSpan === node.span_id}
          selection={focusedSpan == null && selectedSpan === node.span_id ? selection : null}/>
      }) : null}
      {data && !visible.length ? <p role="status">没有匹配的调用</p> : null}
      {visible.length > limit ? <button className="text-button" onClick={() => setLimit(value => value + 20)}>显示更多调用（已显示 {limit} / {visible.length}）</button> : null}
    </div>
  </section>
}
