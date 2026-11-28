import {useState} from 'react'
import {ArrowLeft,Play,SquarePen} from 'lucide-react'
import {api,dateLabel} from '../api'
import DeleteRecord, {traceDeletionBlock} from '../components/DeleteRecord'
import {diagnosisDepthLabel} from '../components/DiagnosisDepthField'
import {Dialog} from '../components/Dialogs'
import TraceDiagnosisDetail, {TraceDiagnosisHeading} from '../components/TraceDiagnosisDetail'
import {isAnalyzing, libraryPath, libraryResultPath, openLibraryTask} from '../traceLibrary'
import type {LibraryTraceDetail as Detail} from '../traceLibrary'
import {useResource,useWorkspace} from '../workspace'

const poll = (detail:Detail) => isAnalyzing(detail.latest?.status) || isAnalyzing(detail.job?.feedback?.status) ? 1800 : 0

export default function LibraryTraceDetail({id,params,onDeleted}: {
  id:string; params:URLSearchParams; onDeleted:(id:string)=>void
}) {
  const {action,busy,go,refresh,version} = useWorkspace()
  const [editingOutcome,setEditingOutcome] = useState(false)
  const [outcome,setOutcome] = useState('')
  const [outcomeError,setOutcomeError] = useState('')
  const jobId = params.get('analysis')
  const path = '/trace-library/' + encodeURIComponent(id) + '/diagnosis'
  const {data,error} = useResource<Detail>(jobId ? path + '?' + new URLSearchParams({job_id:jobId}) : path, version, poll)
  const listParams = new URLSearchParams(params)
  listParams.delete('analysis')
  const back = '#' + libraryPath(listParams)
  const current = '#' + libraryResultPath(id, params)
  if (!data) return <div className="empty">
    {error ? <><h1>无法读取轨迹分析</h1><p role="alert">{error}</p><button className="secondary" onClick={refresh}>重新加载</button>
      <a href={jobId ? current : back}>{jobId ? '查看当前分析' : '返回轨迹库'}</a></> : '正在读取轨迹与分析结果…'}
  </div>

  const {trace,job,latest,selected,task_count} = data
  const pinned = !!jobId && jobId !== data.current_job_id
  const running = isAnalyzing(latest?.status)
  const showingPrevious = !jobId && !!job && !!latest && latest.job_id !== job.id
  const needsOutcome = trace.data.outcome === 'unknown'

  async function saveOutcome() {
    if (!outcome || busy) return
    setOutcomeError('')
    const saved = await action(() => api<{outcome:string}>('/trace-library/' + encodeURIComponent(id) + '/outcome', {
      method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({outcome}),
    }), '已补充业务结果', setOutcomeError)
    if (saved) setEditingOutcome(false)
  }
  const context = selected ? <>
    {selected.task_id ? <a href={'#/tasks/' + encodeURIComponent(selected.task_id)}>{selected.task_name ?? '来源任务'}</a> : '独立分析'}
    {' · ' + [dateLabel(selected.created_at), selected.model,
      selected.diagnosis_mode && diagnosisDepthLabel(selected.diagnosis_mode)].filter(Boolean).join(' · ')}
  </> : undefined
  const header = () => <>
    <div className="ws-breadcrumb"><a href={back}>轨迹库</a><span>/</span><span>轨迹详情</span>
      {jobId ? <a href={current}>查看当前分析</a> : null}
    </div>
    <TraceDiagnosisHeading trace={trace} status={(jobId ? selected?.status : latest?.status) ?? 'pending'}
      context={context} actions={<div className="ws-detail-nav">
        <a className="secondary" href={back}><ArrowLeft size={15}/>返回轨迹库</a>
        <DeleteRecord id={id} name={trace.data.title} kind="trace" blocked={traceDeletionBlock(task_count, running)}
          onDeleted={() => {onDeleted(id); go(libraryPath(listParams), true)}}/>
      </div>}/>
    {showingPrevious ? <div className="ws-warning trace-result-source">
      <span>{running ? '新分析正在处理，当前显示上次完成的结果。' : '最近一次分析未产出结果，当前显示上次完成的结果。'}</span>
      <a href={'#' + libraryResultPath(id, params, latest.job_id)}>查看本次分析</a>
    </div> : null}
  </>

  return <><TraceDiagnosisDetail key={id + ':' + (jobId ?? 'current')} trace={trace} job={job} header={header} historyParams={listParams}
    activity={jobId ? null : latest} readOnly={pinned || showingPrevious && running}
    actions={jobId ? null : needsOutcome ? <button type="button" className="primary small" disabled={busy}
      onClick={() => {setOutcome('');setOutcomeError('');setEditingOutcome(true)}}>
      <SquarePen size={14} aria-hidden="true"/>补充业务结果
    </button> : <button type="button" className="primary small" disabled={busy || running}
      onClick={() => openLibraryTask(listParams, [{id, title:trace.data.title, outcome:trace.data.outcome,
        input_format:trace.data.input_format ?? undefined, diagnosis:latest?.status}])}>
      <Play size={14} aria-hidden="true"/>{job?.status === 'completed' ? '重新分析' : '启动分析'}
    </button>}/>
    {editingOutcome ? <Dialog title="补充业务结果" onClose={() => setEditingOutcome(false)} busy={busy}>
      <form onSubmit={event => {event.preventDefault();void saveOutcome()}}>
        <div className="modal-body review-form">
          <label>业务结果<select value={outcome} disabled={busy} onChange={event => setOutcome(event.target.value)}>
            <option value="" disabled>请选择业务结果</option><option value="failed">业务失败</option><option value="succeeded">业务成功</option>
          </select></label>
          {outcomeError ? <p className="error-inline" role="alert">{outcomeError}</p> : null}
        </div>
        <footer><button type="button" className="secondary" disabled={busy} onClick={() => setEditingOutcome(false)}>取消</button>
          <button type="submit" className="primary" disabled={!outcome || busy}>保存</button></footer>
      </form>
    </Dialog> : null}
  </>
}
