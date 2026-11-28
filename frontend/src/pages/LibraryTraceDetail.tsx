import {ArrowLeft} from 'lucide-react'
import {playgroundLink} from '../playground'
import {dateLabel} from '../api'
import {patternTaskPath} from '../patterns'
import DeleteRecord, {traceDeletionBlock} from '../components/DeleteRecord'
import ExecutionTrace from '../components/ExecutionTrace'
import TraceDetailLayout from '../components/TraceDetailLayout'
import {TraceContextPanel} from '../components/TraceInfoPanel'
import {businessOutcomes, libraryDiagnosis, libraryPath} from '../traceLibrary'
import type {LibraryHistory} from '../traceLibrary'
import type {Trace} from '../types'
import {Badge, formatLabel, tracePath, useResource, useWorkspace} from '../workspace'

const poll = (history: LibraryHistory) => history.diagnoses.some(row => ['running', 'queued'].includes(row.status)) ? 2500 : 0

export default function LibraryTraceDetail({id, params, onDeleted}: {id: string; params: URLSearchParams; onDeleted: (id: string) => void}) {
  const {go, refresh, version} = useWorkspace()
  const {data: trace, error} = useResource<Trace>(`/traces/${id}`, version)
  const {data: history, error: historyError} = useResource<LibraryHistory>(`/trace-library/${id}/history?latest_only=true`, version, poll)
  const back = '#' + libraryPath(params)
  if (error || !trace) return <div className="empty">
    {error ? <><h1>无法读取轨迹</h1><p role="alert">{error}</p><button className="secondary" onClick={refresh}>重新加载</button>
      <a href={back}>返回轨迹库</a></> : '正在读取轨迹…'}
  </div>

  const {title, outcome, messages, otel, input_format, source, task_description, deepagent, agent_identity} = trace.data
  const tasks = new Map(history?.tasks.map(task => [task.id, task]))
  return <TraceDetailLayout detailsLabel="诊断与关联任务" header={<>
    <div className="ws-breadcrumb"><a href={back}>轨迹库</a><span>/</span><span>轨迹详情</span></div>
    <div className="ws-heading">
      <div><div className="ws-heading-title"><h1>{title}</h1><Badge value={outcome}>{businessOutcomes[outcome as keyof typeof businessOutcomes]}</Badge></div>
        <p>{formatLabel(input_format ?? (otel ? 'otel_genai' : 'messages'))} · {deepagent ? `${deepagent.span_count} 个调用` : `${messages.length} 条消息`}{agent_identity?<span className="trace-detail-agent"> · Agent <strong>{agent_identity.name}</strong></span>:null}</p></div>
      <div className="ws-actions">
        <a className="secondary" href={back}><ArrowLeft size={15}/>返回轨迹库</a>
        <a className="secondary" href={playgroundLink(id)}>在 Playground 调试</a>
        <DeleteRecord id={id} name={title} kind="trace"
          blocked={history ? traceDeletionBlock(history.tasks.length, history.diagnoses.some(row => ['queued', 'running'].includes(row.status))) : undefined}
          onDeleted={() => {onDeleted(id); go(libraryPath(params), true)}}/>
      </div>
    </div>
    <div className="trace-detail-disclosures"><TraceContextPanel description={task_description} source={source}/></div>
    </>} trace={<ExecutionTrace key={id} trace={trace} criticalStep={null} selection={null}/>}
    details={<section className="ws-surface library-history" aria-busy={!history && !historyError}>
        <h2>当前诊断</h2>
        <div className="ws-content-scroll">
        {historyError ? <div role="alert" className="error-inline">{historyError} <button className="text-button" onClick={refresh}>重试</button></div> :
          !history ? <p className="muted">正在读取诊断记录…</p> : history.diagnoses.length ?
          <ul>{history.diagnoses.map(row => {
            const href = row.task_id && row.run_id
              ? '#' + tracePath(row.task_id, id, new URLSearchParams()) : null
            return <li key={row.job_id}>
              <div><strong>{row.task_id ? tasks.get(row.task_id)?.name ?? '历史诊断' : '独立诊断'}</strong>
                <Badge value={row.status}>{libraryDiagnosis[row.status as keyof typeof libraryDiagnosis]}</Badge></div>
              <small>{dateLabel(row.created_at)}{row.model ? ` · ${row.model}` : ''}</small>
              {href ? <a href={href}>查看诊断结果</a> : row.status === 'completed' ? <a href={`/api/jobs/${row.job_id}/export?format=md`} download>导出诊断报告</a> : null}
            </li>
          })}</ul> : <p className="muted">这条轨迹暂无诊断记录。</p>}
        <h2>关联任务{history ? `（${history.tasks.length}）` : ''}</h2>
        {history ? history.tasks.length ? <ul>{history.tasks.map(task => <li key={task.id}>
          <a href={task.kind==='failure_patterns'?patternTaskPath(task.id):`#/tasks/${task.id}`}>{task.name}</a>
          {task.agent || task.version ? <small>{[task.agent, task.version].filter(Boolean).join(' · ')}</small> : null}
        </li>)}</ul> : <p className="muted">这条轨迹暂无关联任务。</p> : null}
        </div>
      </section>}/>
}
