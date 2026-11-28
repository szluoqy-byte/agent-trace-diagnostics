import {useCallback,useEffect,useState} from 'react'
import {ArrowLeft,ChevronLeft,ChevronRight,Download,Play,RotateCcw,Terminal} from 'lucide-react'
import {playgroundLink} from '../playground'
import {uiText} from '../branding'
import {dateLabel,post} from '../api'
import type {Evidence} from '../types'
import {ExportDialog,ReviewDialog} from '../components/Dialogs'
import Citation from '../components/Citation'
import {patternTaskPath} from '../patterns'
import type {PatternMembers} from '../patterns'
import Inspector from '../components/Inspector'
import {modelCriticalStep} from '../components/DiagnosisComparison'
import DiagnosisUsage from '../components/DiagnosisUsage'
import ExecutionTrace from '../components/ExecutionTrace'
import TraceDetailLayout from '../components/TraceDetailLayout'
import {TraceContextPanel} from '../components/TraceInfoPanel'
import {Badge,diagnosisStatus,filterQuery,formatLabel,taskPath,tracePath,useResource,useWorkspace} from '../workspace'
import type {ActionResult,AnalysisTask,TraceDetail} from '../workspace'

const pollDetail=(data:TraceDetail)=>[data.job?.status,data.job?.feedback?.status].some(status=>status==='queued'||status==='running')?1800:0

export default function TrajectoryDetail({id,traceId,params}:{id:string;traceId:string;params:URLSearchParams}) {
  const {version,busy,action,go,refresh,notify}=useWorkspace()
  const {data:task,error:taskError}=useResource<AnalysisTask>(`/tasks/${id}`,version,2500)
  const patternTask=params.get('pattern_task'),patternRun=params.get('pattern_run')
  const evidenceRun=patternTask&&patternRun?params.get('run'):null
  const runId=evidenceRun||task?.latest_run_id||(task?'draft':null)
  const draft=runId==='draft'
  const filters=filterQuery(params),{data,error}=useResource<TraceDetail>(runId?`/tasks/${id}/runs/${runId}/traces/${traceId}${filters?'?'+filters:''}`:null,version,pollDetail)
  const {data:snapshot,error:snapshotError}=useResource<PatternMembers>(patternTask&&patternRun
    ? `/pattern-tasks/${encodeURIComponent(patternTask)}/runs/${encodeURIComponent(patternRun)}/members?`+new URLSearchParams({trace_id:traceId})
    : null)
  const frozen=snapshot?.items.find(item=>item.source_task_id===id&&item.source_run_id===runId&&item.job_id===data?.job?.id)
  const [selection,setSelection]=useState<Evidence|null>(null),[dialog,setDialog]=useState('')
  const close=useCallback(()=>setDialog(''),[])
  const evidenceStep=params.get('evidence_step')
  const messageCount=data?.trace.data.messages.length??0
  useEffect(()=>{
    const step=evidenceStep!==null&&/^\d+$/.test(evidenceStep)?Number(evidenceStep):null
    setSelection(step!==null&&step<messageCount?{step,quote:''}:null)
    setDialog('')
  },[id,traceId,runId,data?.job?.id,evidenceStep,messageCount])
  useEffect(()=>{
    const anchor=frozen?.evidence
    if(anchor)setSelection(anchor.source_kind==='compressed'?{step:anchor.step,quote:''}:anchor)
  },[frozen])
  if(error&&!data||taskError&&!task)return <div className="empty"><h1>无法读取轨迹</h1><p role="alert">{error||taskError}</p><button className="secondary" onClick={refresh}>重试加载</button><a href={`#/tasks/${id}`}>返回任务</a></div>
  if(!data||!task)return <div className="empty">正在读取轨迹与分析结果…</div>
  const {trace,job,navigation}=data
  const report=job?.result??null,review=job?.reviews?.[0]?.data
  const readonly=data.read_only
  const inspectorJob=job&&readonly?{...job,read_only:true,feedback_eligible:false,feedback_unavailable_reason:'历史分析记录只读'}:job
  const modelStep=modelCriticalStep(report)
  const legacy=!!report&&report.schema_version!==2,critical=legacy?null:review?review.decision==='rejected'?null:review.critical_step:modelStep
  const query=new URLSearchParams(params);query.delete('pattern_task');query.delete('pattern_run');query.delete('pattern_id');query.delete('evidence_step');query.delete('run');query.delete('improvement')
  const size=[10,20,50].includes(Number(params.get('page_size')))?Number(params.get('page_size')):10
  function back(){if(navigation.index>=0)query.set('page',String(Math.floor(navigation.index/size)+1));query.set('focus',traceId);go(taskPath(id,query))}
  function next(target:string|null,direction:number){if(!target)return;query.delete('focus');if(navigation.index>=0)query.set('page',String(Math.floor((navigation.index+direction)/size)+1));go(tracePath(id,target,query))}
  async function diagnose(){await action(()=>post<ActionResult>(`/tasks/${id}/runs/${runId}/actions`,{action:'diagnose',trace_ids:[traceId]}),'本条轨迹已加入诊断队列')}
  async function feedback(){await action(()=>post(`/jobs/${job!.id}/feedback`),'改进建议任务已提交，生成后会自动展示')}
  async function regenerate(){
    if (!job) return
    await action(()=>post(`/jobs/${job.id}/regenerate`),'本条轨迹已加入重新诊断队列')
  }
  async function regenerateFeedback(){
    if (!job || job.feedback?.status !== 'completed') return
    const feedbackId = job.feedback.id
    await action(()=>post(`/jobs/${job.id}/feedback/regenerate`,{feedback_id:feedbackId}),'已提交重新生成改进建议')
  }
  const canRun=!readonly&&(!job||job.status==='failed')
  const blocked=trace.data.outcome==='unknown'
  const canRegenerate=!readonly&&!draft&&!blocked&&job?.mode==='live'&&job.status==='completed'
  return <><TraceDetailLayout detailsLabel="诊断结果" selection={selection} header={<>
  <div className="ws-breadcrumb"><a href="#/tasks">分析任务</a><span>/</span><a href={'#'+taskPath(id,query)} onClick={e=>{e.preventDefault();back()}}>{task.name}</a><span>/</span><span>{trace.data.title}</span></div><div className="ws-heading ws-detail-heading"><div><div className="ws-heading-title"><h1>{trace.data.title}</h1><Badge value={trace.data.outcome}>{trace.data.outcome==='failed'?'业务失败':trace.data.outcome==='succeeded'?'业务成功':'结果未知'}</Badge><Badge value={job?.status??'pending'}>{diagnosisStatus[job?.status??'pending']}</Badge></div><p>{draft?'任务草稿':readonly?'引用的分析结果':'当前分析'} · {trace.data.deepagent ? `${trace.data.deepagent.span_count} 个调用` : `${trace.data.messages.length} 条消息`} · {formatLabel(trace.data.input_format ?? (trace.data.otel ? 'otel_genai' : 'messages'))}{trace.data.agent_identity?<span className="trace-detail-agent"> · Agent <strong>{trace.data.agent_identity.name}</strong></span>:null}</p></div><div className="ws-detail-nav"><button className="secondary" onClick={back}><ArrowLeft size={15}/>返回任务</button><span>{navigation.index>=0?`筛选结果 ${navigation.index+1} / ${navigation.total}`:'该轨迹不符合当前筛选条件'}</span><button className="secondary" disabled={!navigation.previous_id} onClick={()=>next(navigation.previous_id,-1)}><ChevronLeft size={15}/>上一条</button><button className="secondary" disabled={!navigation.next_id} onClick={()=>next(navigation.next_id,1)}>下一条<ChevronRight size={15}/></button></div></div>
  {patternTask&&patternRun?<div className="ws-warning pattern-source-banner">
    <a href={patternTaskPath(patternTask,patternRun)+(params.get('pattern_id')?'&'+new URLSearchParams({pattern_id:params.get('pattern_id')!}):'')}>返回失败模式分析</a>
    {frozen?<><span>失败模式分析引用的诊断{frozen.critical_step!=null?` · 关键步骤 #${frozen.critical_step}`:''}</span>
      {frozen.review_id!==(job?.reviews?.[0]?.id??null)?<span>该分析引用的复核记录与当前版本不同。</span>:null}
      {frozen.evidence?<details><summary>查看引用证据</summary><Citation evidence={frozen.evidence} views={report?.compression_views} onJump={setSelection}/></details>:null}
    </>:snapshotError?<span>无法读取失败模式分析使用的诊断记录。</span>:null}
  </div>:null}
  {readonly&&!draft?<div className="ws-warning">正在核对历史报告引用的诊断。</div>:null}
  {blocked?<p className="ws-warning">该轨迹缺少业务结果。请重新上传并填写业务结果。</p>:null}
  {job?.status==='queued'||job?.status==='running'?<progress className="diagnosis-progress" max={100} value={job.progress} aria-label="诊断进度"/>:null}
  <div className="trace-detail-tools">
    <div className="trace-detail-disclosures">
      <TraceContextPanel description={trace.data.task_description}/>
      {report?<DiagnosisUsage report={report}/>:null}
      {data.attempts.length>1?<details className="ws-attempts"><summary>诊断请求记录 · {data.attempts.length} 次尝试</summary><div className="trace-disclosure-body">{data.attempts.map(a=><div key={a.id}><span>第 {a.attempt} 次 · {dateLabel(a.created_at)} · {diagnosisStatus[a.status]}</span>{a.error?<small>{uiText(a.error)}</small>:null}</div>)}</div></details>:null}
    </div>
    <div className="ws-detail-status">
      {job?.status!=='completed'?<span>{uiText(job?.stage)||'尚未开始分析'}{job?.status==='running'?` · ${job.progress}%`:''}</span>:null}
      <div className="ws-actions">
        {canRun?<button className="primary small" disabled={busy||blocked} onClick={()=>void diagnose()}>{job?<RotateCcw size={14}/>:<Play size={14}/>} {job?'重试诊断':'诊断轨迹'}</button>:null}
        <a className="secondary small" href={playgroundLink(traceId)}><Terminal size={14} aria-hidden="true"/>在 Playground 调试</a>
        <button className="secondary small" disabled={job?.status!=='completed'} onClick={()=>setDialog('export')}><Download size={14} aria-hidden="true"/>导出诊断报告</button>
      </div>
    </div>
  </div>
  </>} trace={<ExecutionTrace key={`${runId}-${traceId}`} trace={trace} criticalStep={critical} selection={selection}/>}
    details={<Inspector draft={draft} key={`${runId}-${traceId}-${job?.id??'pending'}`} job={inspectorJob} report={report} onJump={e=>setSelection({...e})} onReview={()=>setDialog('review')} onFeedback={job&&!readonly?feedback:undefined}
      busy={busy} onRegenerate={canRegenerate?regenerate:undefined} onRegenerateFeedback={job&&!readonly?regenerateFeedback:undefined}/>}/>
  {dialog==='review'&&job?.result?.attribution&&!readonly?<ReviewDialog job={job} messages={trace.data.messages} onClose={close} onDone={()=>{close();refresh();notify('复核已保存')}}/>:null}{dialog==='export'&&job?<ExportDialog job={job} onClose={close}/>:null}</>
}
