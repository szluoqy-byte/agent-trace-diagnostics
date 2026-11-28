import {ArrowLeft,ChevronLeft,ChevronRight,Play,RotateCcw} from 'lucide-react'
import {uiText} from '../branding'
import {dateLabel,post} from '../api'
import type {Evidence} from '../types'
import Citation from '../components/Citation'
import {patternTaskPath} from '../patterns'
import type {PatternMembers} from '../patterns'
import TraceDiagnosisDetail, {TraceDiagnosisHeading} from '../components/TraceDiagnosisDetail'
import {diagnosisStatus,filterQuery,taskPath,tracePath,useResource,useWorkspace} from '../workspace'
import type {ActionResult,AnalysisTask,TraceDetail} from '../workspace'
import {isAnalyzing} from '../traceLibrary'

const pollDetail = (data:TraceDetail) => isAnalyzing(data.job?.status) || isAnalyzing(data.job?.feedback?.status) ? 1800 : 0

export default function TrajectoryDetail({id,traceId,params}:{id:string;traceId:string;params:URLSearchParams}) {
  const {version,busy,action,go,refresh} = useWorkspace()
  const {data:task,error:taskError} = useResource<AnalysisTask>(`/tasks/${id}`, version, 2500)
  const patternTask = params.get('pattern_task'), patternRun = params.get('pattern_run')
  const evidenceRun = patternTask && patternRun ? params.get('run') : null
  const runId = evidenceRun || task?.latest_run_id || (task ? 'draft' : null)
  const draft = runId === 'draft'
  const filters = filterQuery(params)
  const {data,error} = useResource<TraceDetail>(runId
    ? `/tasks/${id}/runs/${runId}/traces/${traceId}${filters ? '?' + filters : ''}` : null, version, pollDetail)
  const {data:snapshot,error:snapshotError} = useResource<PatternMembers>(patternTask && patternRun
    ? `/pattern-tasks/${encodeURIComponent(patternTask)}/runs/${encodeURIComponent(patternRun)}/members?` + new URLSearchParams({trace_id:traceId})
    : null)
  if (error && !data || taskError && !task) return <div className="empty"><h1>无法读取轨迹</h1><p role="alert">{error || taskError}</p>
    <button className="secondary" onClick={refresh}>重试加载</button><a href={`#/tasks/${id}`}>返回任务</a></div>
  if (!data || !task) return <div className="empty">正在读取轨迹与分析结果…</div>

  const {trace,job,navigation,read_only:readOnly} = data
  const frozen = snapshot?.items.find(item => item.source_task_id === id && item.source_run_id === runId && item.job_id === job?.id)
  const evidenceStep = params.get('evidence_step')
  const step = evidenceStep !== null && /^\d+$/.test(evidenceStep) ? Number(evidenceStep) : null
  const initialSelection = frozen?.evidence
    ? frozen.evidence.source_kind === 'compressed' ? {step:frozen.evidence.step, quote:''} : frozen.evidence
    : step !== null && step < trace.data.messages.length ? {step, quote:''} : null
  const query = new URLSearchParams(params)
  for (const key of ['pattern_task','pattern_run','pattern_id','evidence_step','run','improvement']) query.delete(key)
  const size = [10,20,50].includes(Number(params.get('page_size'))) ? Number(params.get('page_size')) : 10

  function back() {
    if (navigation.index >= 0) query.set('page', String(Math.floor(navigation.index / size) + 1))
    query.set('focus', traceId)
    go(taskPath(id, query))
  }

  function next(target:string|null, direction:number) {
    if (!target) return
    query.delete('focus')
    if (navigation.index >= 0) query.set('page', String(Math.floor((navigation.index + direction) / size) + 1))
    go(tracePath(id, target, query))
  }

  async function diagnose() {
    await action(() => post<ActionResult>(`/tasks/${id}/runs/${runId}/actions`,
      {action:'diagnose', trace_ids:[traceId]}), '本条轨迹已加入诊断队列')
  }

  const canRun = !readOnly && (!job || job.status === 'failed')
  const header = (jump:(evidence:Evidence)=>void) => <>
    <div className="ws-breadcrumb">
      <a href="#/tasks">分析任务</a><span>/</span>
      <a href={'#' + taskPath(id,query)} onClick={event => {event.preventDefault();back()}}>{task.name}</a>
      <span>/</span><span>{trace.data.title}</span>
    </div>
    <TraceDiagnosisHeading trace={trace} status={job?.status ?? 'pending'} context={draft ? '任务草稿' : readOnly ? '引用的分析结果' : '当前分析'}
      actions={<div className="ws-detail-nav">
        <button className="secondary" onClick={back}><ArrowLeft size={15}/>返回任务</button>
        <span>{navigation.index >= 0 ? `筛选结果 ${navigation.index + 1} / ${navigation.total}` : '该轨迹不符合当前筛选条件'}</span>
        <button className="secondary" disabled={!navigation.previous_id} onClick={() => next(navigation.previous_id,-1)}><ChevronLeft size={15}/>上一条</button>
        <button className="secondary" disabled={!navigation.next_id} onClick={() => next(navigation.next_id,1)}>下一条<ChevronRight size={15}/></button>
      </div>}/>
    {patternTask && patternRun ? <div className="ws-warning pattern-source-banner">
      <a href={patternTaskPath(patternTask,patternRun)+(params.get('pattern_id')?'&'+new URLSearchParams({pattern_id:params.get('pattern_id')!}):'')}>返回失败模式分析</a>
      {frozen ? <>
        <span>失败模式分析引用的诊断{frozen.critical_step != null ? ` · 关键步骤 #${frozen.critical_step}` : ''}</span>
        {frozen.review_id !== (job?.reviews?.[0]?.id ?? null) ? <span>该分析引用的复核记录与当前版本不同。</span> : null}
        {frozen.evidence ? <details><summary>查看引用证据</summary><Citation evidence={frozen.evidence}
          views={job?.result?.compression_views} onJump={jump}/></details> : null}
      </> : snapshotError ? <span>无法读取失败模式分析使用的诊断记录。</span> : null}
    </div> : null}
  </>

  return <TraceDiagnosisDetail key={JSON.stringify([runId,traceId,job?.id,evidenceStep,patternTask,patternRun,params.get('pattern_id')])} trace={trace} job={job}
    header={header} readOnly={readOnly} draft={draft} initialSelection={initialSelection}
    actions={canRun ? <button className="primary small" disabled={busy || trace.data.outcome === 'unknown'} onClick={() => void diagnose()}>
      {job ? <RotateCcw size={14}/> : <Play size={14}/>} {job ? '重试诊断' : '诊断轨迹'}
    </button> : null}
    disclosures={data.attempts.length > 1 ? <details className="ws-attempts">
      <summary>诊断请求记录 · {data.attempts.length} 次尝试</summary>
      <div className="trace-disclosure-body">{data.attempts.map(attempt => <div key={attempt.id}>
        <span>第 {attempt.attempt} 次 · {dateLabel(attempt.created_at)} · {diagnosisStatus[attempt.status]}</span>
        {attempt.error ? <small>{uiText(attempt.error)}</small> : null}
      </div>)}</div>
    </details> : null}/>
}
