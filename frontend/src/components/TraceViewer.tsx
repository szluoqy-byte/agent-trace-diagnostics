import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, ChevronDown } from 'lucide-react'
import type { Evidence, Message, OTelProvenance, OTelSpan } from '../types'

function MessageSources({message, spans}: {message:Message; spans:Map<string,OTelSpan>}) {
  const [open, setOpen] = useState(false)
  return <details className="otel-message-source" onToggle={event => setOpen(event.currentTarget.open)}><summary>OTel 来源 · {message.source_refs?.length} 处</summary>{open ? message.source_refs?.map((ref, i) => {
    const record = spans.get(ref.span_id.toLowerCase())
    return <div key={i}><dl><dt>Trace ID</dt><dd>{ref.trace_id}</dd><dt>Span ID</dt><dd>{ref.span_id}</dd><dt>消息位置</dt><dd>{ref.attribute}{ref.message_index != null ? `[${ref.message_index}]` : ''}</dd></dl>{record ? <><small>{record.location}</small><pre aria-label={`原始 span ${ref.span_id}`}>{JSON.stringify(record, null, 2)}</pre></> : <p className="muted">原始 span 未保存</p>}</div>
  }) : null}</details>
}

interface Props {
  messages: Message[]
  criticalStep: number | null
  selection: Evidence | null
  otel?: OTelProvenance | null
  sourceCalls?: ReadonlyMap<number, string>
  onOpenCall?: (spanId: string) => void
}
export default function TraceViewer({messages, criticalStep, selection, otel, sourceCalls, onOpenCall}: Props) {
  const [query, setQuery] = useState('')
  const [role, setRole] = useState('all')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const viewport = useRef<HTMLDivElement>(null)
  const spans = useMemo(() => new Map(otel?.spans.map(record => [record.span.spanId.toLowerCase(), record])), [otel])
  useEffect(() => {
    if (!selection || selection.step == null) return
    const step = selection.step
    setQuery(''); setRole('all'); setExpanded(old => new Set([...old, step]))
    const timer = setTimeout(() => {
      const message = viewport.current?.querySelector<HTMLElement>(`[data-step="${step}"]`)
      if(!message)return
      message.focus({preventScroll:true})
      message.scrollIntoView({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'})
    },50)
    return () => clearTimeout(timer)
  }, [selection])
  const visible = messages.filter(m => (role === 'all' || role === m.role) && m.content.toLowerCase().includes(query.toLowerCase()))
  function renderContent(message: Message) {
    let content = message.content
    if (content.length > 900 && !expanded.has(message.step)) {
      const match = query ? content.toLowerCase().indexOf(query.toLowerCase()) : -1
      content = match >= 0 ? (match > 160 ? '…' : '') + content.slice(Math.max(0,match-160),match+query.length+240) + '\n…' : content.slice(0,500) + '\n…'
    }
    const quote = selection?.step === message.step ? selection.quote : query
    const sourceStart = selection?.step === message.step && selection.start != null && content === message.content ? Array.from(content).slice(0,selection.start).join('').length : -1
    const index = selection?.step === message.step ? (sourceStart >= 0 && content.startsWith(quote,sourceStart) ? sourceStart : content.indexOf(quote)) : quote ? content.toLowerCase().indexOf(quote.toLowerCase()) : -1
    return quote && index >= 0 ? <>{content.slice(0,index)}<mark>{content.slice(index,index + quote.length)}</mark>{content.slice(index + quote.length)}</> : content
  }
  return <section className="execution-messages">
    <div className="panel-heading"><h3>诊断步骤</h3><label className="search-box"><Search size={15}/><input type="search" aria-label="搜索轨迹内容" placeholder="搜索轨迹内容…" value={query} onChange={e => setQuery(e.target.value)}/></label></div>
    <div className="role-filter"><button className={role === 'all' ? 'active' : ''} aria-pressed={role==='all'} onClick={() => setRole('all')}>全部角色</button>{['user','assistant','tool','system'].map(r => <button key={r} className={role === r ? 'active' : ''} aria-pressed={role===r} onClick={() => setRole(r)}>{r}</button>)}<span aria-live="polite">{visible.length} 条</span></div>
    <div className="messages" ref={viewport}>{visible.length ? visible.map(m => <article tabIndex={-1} data-step={m.step} id={'step-' + m.step} key={m.step} className={'message ' + (m.step === criticalStep ? 'critical ' : '') + (selection?.step === m.step ? 'focused' : '')}>
      <span className="timeline-node"/><span className="step-number">#{m.step}</span><div className="message-main"><div className="message-meta"><span className={'role role-' + m.role}>{m.role}</span>{m.name && m.name !== m.role ? <span className="muted">{m.name}</span> : null}{m.step === criticalStep ? <span className="critical-label">关键步骤</span> : null}<span className="source-step">{m.source_refs?.length ? 'OTel GenAI' : `原始 #${m.source_step}`}</span></div>
      <pre>{renderContent(m)}</pre>{m.content.length > 900 ? <button className="text-button expand-button" aria-expanded={expanded.has(m.step)} onClick={() => setExpanded(old => {const next=new Set(old); if(next.has(m.step)) next.delete(m.step); else next.add(m.step); return next})}><ChevronDown size={13}/>{expanded.has(m.step) ? '收起内容' : `展开完整内容 (${m.content.length.toLocaleString()} 字符)`}</button> : null}
      {sourceCalls?.has(m.step) && onOpenCall ? <button type="button" className="text-button" onClick={() => onOpenCall(sourceCalls.get(m.step)!)}>查看来源调用</button> : null}
      {m.source_refs?.length ? <MessageSources message={m} spans={spans}/> : null}</div>
    </article>) : <div className="empty">没有匹配的消息<button className="secondary small" onClick={()=>{setQuery('');setRole('all')}}>清除搜索和筛选</button></div>}</div>
    <div className="panel-foot">{otel ? '步骤从 #0 开始 · 按消息历史还原 · 可展开查看原始 span' : '步骤从 #0 开始 · 原始消息顺序与内容保留'}</div>
  </section>
}
