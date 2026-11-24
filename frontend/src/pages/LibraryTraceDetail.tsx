import {ArrowLeft} from 'lucide-react'
import {dateLabel} from '../api'
import {sourceLabel} from '../branding'
import {patternTaskPath} from '../patterns'
import OTelOverview from '../components/OTelOverview'
import DeepAgentOverview from '../components/DeepAgentOverview'
import TraceViewer from '../components/TraceViewer'
import {businessOutcomes, libraryDiagnosis, libraryPath} from '../traceLibrary'
import type {LibraryHistory} from '../traceLibrary'
import type {Trace} from '../types'
import {Badge, formatLabel, tracePath, useResource, useWorkspace} from '../workspace'

const poll = (history: LibraryHistory) => history.diagnoses.some(row => ['running', 'queued'].includes(row.status)) ? 2500 : 0

export default function LibraryTraceDetail({id, params}: {id: string; params: URLSearchParams}) {
  const {refresh, version} = useWorkspace()
  const {data: trace, error} = useResource<Trace>(`/traces/${id}`, version)
  const {data: history, error: historyError} = useResource<LibraryHistory>(`/trace-library/${id}/history`, version, poll)
  const back = '#' + libraryPath(params)
  if (error || !trace) return <div className="empty">
    {error ? <><h1>无法读取轨迹</h1><p role="alert">{error}</p><button className="secondary" onClick={refresh}>重新加载</button>
      <a href={back}>返回轨迹库</a></> : '正在读取轨迹…'}
  </div>

  const {title, outcome, messages, otel, input_format, source, task_description} = trace.data
  const tasks = new Map(history?.tasks.map(task => [task.id, task]))
  return <>
    <div className="ws-breadcrumb"><a href={back}>轨迹库</a><span>/</span><span>轨迹详情</span></div>
    <div className="ws-heading">
      <div><div className="ws-heading-title"><h1>{title}</h1><Badge value={outcome}>{businessOutcomes[outcome as keyof typeof businessOutcomes]}</Badge></div>
        <p>{formatLabel(input_format ?? (otel ? 'otel_genai' : 'messages'))} · {messages.length} 条消息{source ? ` · ${sourceLabel(source)}` : ''}</p></div>
      <a className="secondary" href={back}><ArrowLeft size={15}/>返回轨迹库</a>
    </div>
    {task_description ? <details className="ws-trace-context"><summary>轨迹任务说明</summary><p>{task_description}</p></details> : null}
    <OTelOverview trace={trace}/>
    <DeepAgentOverview key={trace.id} trace={trace}/>
    <div className="library-detail-grid">
      <TraceViewer key={id} messages={messages} criticalStep={null} selection={null} otel={otel}/>
      <section className="ws-surface library-history" aria-busy={!history && !historyError}>
        <h2>诊断记录</h2>
        {historyError ? <div role="alert" className="error-inline">{historyError} <button className="text-button" onClick={refresh}>重试</button></div> :
          !history ? <p className="muted">正在读取诊断记录…</p> : history.diagnoses.length ?
          <ul>{history.diagnoses.map(row => {
            const href = row.task_id && row.run_id
              ? '#' + tracePath(row.task_id, id, new URLSearchParams({run: row.run_id})) : null
            return <li key={row.job_id}>
              <div><strong>{row.task_id ? tasks.get(row.task_id)?.name ?? '历史诊断' : '独立诊断'}</strong>
                <Badge value={row.status}>{libraryDiagnosis[row.status as keyof typeof libraryDiagnosis]}</Badge></div>
              <small>{row.number == null ? '' : `第 ${row.number} 次分析 · `}{dateLabel(row.created_at)}{row.model ? ` · ${row.model}` : ''}</small>
              {href ? <a href={href}>查看诊断结果</a> : row.status === 'completed' ? <a href={`/api/jobs/${row.job_id}/export?format=md`} download>导出诊断报告</a> : null}
            </li>
          })}</ul> : <p className="muted">这条轨迹暂无诊断记录。</p>}
        <h2>关联任务{history ? `（${history.tasks.length}）` : ''}</h2>
        {history ? history.tasks.length ? <ul>{history.tasks.map(task => <li key={task.id}>
          <a href={task.kind==='failure_patterns'?patternTaskPath(task.id):`#/tasks/${task.id}`}>{task.name}</a>
          {task.agent || task.version ? <small>{[task.agent, task.version].filter(Boolean).join(' · ')}</small> : null}
        </li>)}</ul> : <p className="muted">这条轨迹暂无关联任务。</p> : null}
      </section>
    </div>
  </>
}
