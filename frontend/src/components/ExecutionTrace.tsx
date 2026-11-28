import {useEffect, useId, useMemo, useRef, useState} from 'react'
import type {Evidence, Trace} from '../types'
import {playgroundLink} from '../playground'
import {useResource} from '../workspace'
import CallTimeline, {durationLabel} from './CallTimeline'
import Tabs from './Tabs'
import TraceViewer from './TraceViewer'

interface Call {
  span_id: string
  parent_id: string | null
  title: string
  kind: string | number
  duration_us: number | null
  start_offset_us: number | null
  end_offset_us: number | null
  depth: number
  status_code: string | number | null
  finish_reason: string | null
  input: unknown
  output: unknown
  raw: unknown
}

interface ExecutionView {
  calls: Call[]
  message_sources: {step: number; span_id: string; primary: boolean}[]
}

const timelineTab = {id:'timeline', label:'调用时序'}
const callsTab = {id:'calls', label:'调用列表'}
const stepsTab = {id:'steps', label:'诊断步骤'}

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

function CallRecord({call, traceId, parentTitle, steps, critical, focused, onStep}: {
  traceId: string
  call: Call & {number: number}; parentTitle?: string; steps: number[]; critical: boolean;
  focused: boolean; onStep: (step: number) => void
}) {
  const record = useRef<HTMLDetailsElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const element = record.current
    if (!focused || !element) return
    element.open = true
    setOpen(true)
    element.querySelector('summary')?.focus({preventScroll:true})
    element.scrollIntoView?.({block:'nearest'})
  }, [focused])
  const hasModelInput = call.input != null && ['LLM', 'chat', 'text_completion', 'generate_content'].includes(String(call.kind))

  return <details ref={record} className={`execution-call${critical ? ' execution-critical' : ''}`}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary style={{marginInlineStart: Math.min(call.depth, 5) * 8}}>
      <strong>#{call.number} {call.title}</strong>
      <span className="execution-kind">{call.kind}</span>
      <span className="execution-duration">{durationLabel(call.duration_us)}</span>
      {critical ? <span className="critical-label">关键调用</span> : null}
    </summary>
    {open ? <div className="execution-call-body">
      <div className="execution-call-meta">
        <span>Span ID <code>{call.span_id}</code></span>
        {call.parent_id ? <span>父调用 {parentTitle || call.parent_id}</span> : null}
        {call.status_code != null && call.status_code !== '' ? <span>Span 状态码 <code>{call.status_code}</code></span> : null}
        {call.finish_reason ? <span>结束原因 <code>{call.finish_reason}</code></span> : null}
      </div>
      <div className="execution-call-io">
        <CapturedField label="输入" value={call.input}/>
        <CapturedField label="输出" value={call.output}/>
      </div>
      {steps.length || hasModelInput ? <div className="execution-step-links">
        {steps.map(step => <button type="button" key={step} className="text-button" onClick={() => onStep(step)}>诊断步骤 #{step}</button>)}
        {hasModelInput ? <a className="text-button" href={playgroundLink(traceId, call.span_id)}>在 Playground 调试此调用</a> : null}
      </div> : null}
      <details>
        <summary>原始 span</summary>
        <pre aria-label={`原始 span ${call.span_id}`}>{JSON.stringify(call.raw, null, 2)}</pre>
      </details>
    </div> : null}
  </details>
}

export default function ExecutionTrace({trace, criticalStep, selection}: {
  trace: Trace; criticalStep: number | null; selection: Evidence | null
}) {
  const {deepagent, otel, messages, input_format} = trace.data
  const path = `/traces/${trace.id}`
  const download = deepagent ? `${path}/deepagent?download=true` : `${path}/otel`
  const tabsId = useId()
  const [tab, setTab] = useState<string>()
  const [focusedSpan, setFocusedSpan] = useState<string | null>(null)
  const [focusedStep, setFocusedStep] = useState<Evidence | null>(null)
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(20)
  const [version, setVersion] = useState(0)
  const {data, error, loading} = useResource<ExecutionView>(deepagent || otel ? `${path}/execution` : null, version)
  const {calls, byId, byStep, stepsBySpan, totalUs} = useMemo(() => {
    const calls = (data?.calls ?? []).map((call, index) => ({...call, number: index + 1}))
    const byId = new Map(calls.map(call => [call.span_id, call]))
    const stepsBySpan = new Map<string, number[]>()
    const byStep = new Map<number, string>()
    for (const source of data?.message_sources ?? []) {
      if (!source.primary || !byId.has(source.span_id)) continue
      const steps = stepsBySpan.get(source.span_id) ?? []
      steps.push(source.step)
      stepsBySpan.set(source.span_id, steps)
      byStep.set(source.step, source.span_id)
    }
    const totalUs = calls.reduce<number | null>((total, call) => call.start_offset_us == null || call.end_offset_us == null
      ? total : Math.max(total ?? 0, call.end_offset_us), null)
    return {calls, byId, byStep, stepsBySpan, totalUs}
  }, [data])
  const criticalSpan = criticalStep == null ? undefined : byStep.get(criticalStep)

  useEffect(() => {
    setFocusedStep(selection?.step == null ? null : selection)
    if (selection?.step == null) return
    setTab('steps')
  }, [selection])

  function openCall(spanId: string) {
    setFocusedSpan(spanId)
    setQuery('')
    setLimit(limit => Math.max(limit, byId.get(spanId)?.number ?? 0))
    setTab('calls')
  }

  function openStep(step: number) {
    setFocusedStep(selection?.step === step ? {...selection} : {step, quote:''})
    setTab('steps')
  }

  const tabs = totalUs != null ? [timelineTab, callsTab, stepsTab] : calls.length ? [callsTab, stepsTab] : [stepsTab]
  const active = tabs.find(item => item.id === tab)?.id ?? tabs[0].id
  const term = query.trim().toLowerCase()
  const visible = calls.filter(call => !term || [call.title, call.kind, call.span_id].some(value => String(value).toLowerCase().includes(term)))
  const current = visible.slice(0, limit)
  const warnings = otel?.warnings ?? []

  return <section className="panel trace-panel execution-panel" aria-label="执行轨迹">
    <div className="panel-heading">
      <h2>执行轨迹 <span className="count">{calls.length ? `${calls.length} 个调用 · ` : ''}{messages.length} 步</span></h2>
      {deepagent || otel ? <a className="text-button" href={`/api${download}`} download>下载原始 span 和步骤映射</a> : null}
    </div>
    {input_format === 'deepagent' && !deepagent ? <p className="execution-notice">重新上传原文件后可查看调用时序。</p> : null}
    {warnings.length ? <details className="execution-notice"><summary>导入提示（{warnings.length}）</summary>
      <ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
    </details> : null}
    {error ? <p className="execution-notice" role="alert">{error} <button className="text-button" onClick={() => setVersion(value => value + 1)}>重试</button></p> : null}
    {loading ? <p className="execution-notice" role="status">正在读取调用…</p> : null}
    {tabs.length > 1 ? <Tabs id={tabsId} label="执行轨迹视图" className="ws-tabs execution-tabs"
      items={tabs} active={active} onChange={next => {setTab(next); if(next !== 'calls') setFocusedSpan(null)}}/> : null}
    {active === 'steps' && (!loading || selection) ? <div className="execution-messages"
      role={tabs.length > 1 ? 'tabpanel' : undefined} id={`${tabsId}-panel-steps`} aria-labelledby={tabs.length > 1 ? `${tabsId}-tab-steps` : undefined}>
      <TraceViewer messages={messages} criticalStep={criticalStep} selection={focusedStep} otel={otel}
        sourceCalls={byStep} onOpenCall={openCall}/>
    </div> : null}
    {active !== 'steps' ? <>
      <label className="execution-span-search">筛选调用
        <input type="search" value={query} placeholder="名称、类型或 Span ID" onChange={event => {setQuery(event.target.value); setLimit(20)}}/>
      </label>
      <div className="messages execution-calls" role="tabpanel" id={`${tabsId}-panel-${active}`} aria-labelledby={`${tabsId}-tab-${active}`}>
        {active === 'timeline' && totalUs != null && current.length ? <CallTimeline calls={current} totalUs={totalUs} criticalSpan={criticalSpan} onOpen={openCall}/> : null}
        {active === 'calls' ? current.map(call => <CallRecord key={call.span_id} call={call} traceId={trace.id}
          parentTitle={call.parent_id ? byId.get(call.parent_id)?.title : undefined}
          steps={stepsBySpan.get(call.span_id) ?? []} critical={criticalSpan === call.span_id}
          focused={focusedSpan === call.span_id} onStep={openStep}/>) : null}
        {!visible.length ? <p role="status">没有匹配的调用</p> : null}
        {visible.length > limit ? <button className="text-button" onClick={() => setLimit(value => value + 20)}>显示更多调用（已显示 {limit} / {visible.length}）</button> : null}
      </div>
    </> : null}
  </section>
}
