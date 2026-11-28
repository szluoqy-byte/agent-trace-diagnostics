import {useState} from 'react'
import type {ReactNode} from 'react'
import {Download,Terminal} from 'lucide-react'
import {post} from '../api'
import {uiText} from '../branding'
import {playgroundLink} from '../playground'
import {businessOutcomes,isAnalyzing,libraryDiagnosis} from '../traceLibrary'
import type {LibraryDiagnosis} from '../traceLibrary'
import type {Evidence,Job,Trace} from '../types'
import {Badge,formatLabel,useWorkspace} from '../workspace'
import {modelCriticalStep} from './DiagnosisComparison'
import DiagnosisUsage from './DiagnosisUsage'
import {ExportDialog,ReviewDialog} from './Dialogs'
import ExecutionTrace from './ExecutionTrace'
import Inspector from './Inspector'
import TraceAnalysisHistory from './TraceAnalysisHistory'
import TraceDetailLayout from './TraceDetailLayout'
import {TraceContextPanel} from './TraceInfoPanel'

export function TraceDiagnosisHeading({trace, status, context, actions}: {
  trace:Trace; status:string; context?:ReactNode; actions:ReactNode
}) {
  const {title,outcome,deepagent,messages,input_format,otel,agent_identity} = trace.data
  return <div className="ws-heading ws-detail-heading">
    <div>
      <div className="ws-heading-title">
        <h1>{title}</h1>
        <Badge value={outcome}>{businessOutcomes[outcome as keyof typeof businessOutcomes]}</Badge>
        <Badge value={status}>{libraryDiagnosis[status] ?? status}</Badge>
      </div>
      <p>{context ? <>{context} · </> : null}
        {deepagent ? deepagent.span_count + ' 个调用' : messages.length + ' 条消息'} · {formatLabel(input_format ?? (otel ? 'otel_genai' : 'messages'))}
        {agent_identity ? <span className="trace-detail-agent"> · Agent <strong>{agent_identity.name}</strong></span> : null}
      </p>
    </div>
    {actions}
  </div>
}

export default function TraceDiagnosisDetail({trace,job,header,actions,disclosures,activity,initialSelection,historyParams,readOnly=false,draft=false}: {
  trace:Trace; job:Job|null; header:(jump:(evidence:Evidence)=>void)=>ReactNode
  actions?:ReactNode; disclosures?:ReactNode; activity?:LibraryDiagnosis|null; initialSelection?:Evidence|null
  historyParams?:URLSearchParams; readOnly?:boolean; draft?:boolean
}) {
  const {busy,action,refresh,notify} = useWorkspace()
  const [selected,setSelected] = useState<Evidence|null>(null)
  const [dialog,setDialog] = useState<{kind:'review'|'export'; jobId:string}|null>(null)
  const selection = selected ?? initialSelection ?? null
  const jump = (evidence:Evidence) => setSelected({...evidence})
  const close = () => setDialog(null)
  const report = job?.result ?? null
  const review = job?.reviews?.[0]?.data
  const critical = report?.schema_version !== 2 ? null : review
    ? review.decision === 'rejected' ? null : review.critical_step : modelCriticalStep(report)
  const historical = readOnly || !!job?.read_only
  const state = activity ?? job
  const running = isAnalyzing(state?.status)
  const blocked = trace.data.outcome === 'unknown'
  const inspectorJob = job && historical
    ? {...job, read_only:true, feedback_eligible:false, feedback_unavailable_reason:'历史分析记录只读'} : job

  async function feedback() {
    if (job) await action(() => post('/jobs/' + job.id + '/feedback'), '改进建议任务已提交，生成后会自动展示')
  }
  async function regenerate() {
    if (job) await action(() => post('/jobs/' + job.id + '/regenerate'), '本条轨迹已加入重新诊断队列')
  }
  async function regenerateFeedback() {
    if (job?.feedback?.status !== 'completed') return
    const feedbackId = job.feedback.id
    await action(() => post('/jobs/' + job.id + '/feedback/regenerate', {feedback_id:feedbackId}), '已提交重新生成改进建议')
  }
  const canRegenerate = !historical && !draft && !blocked && !running && job?.mode === 'live' && job.status === 'completed'

  return <><TraceDetailLayout detailsLabel="诊断结果" selection={selection} header={<>
    {header(jump)}
    {historical && !draft && !activity ? <div className="ws-warning">正在查看历史分析结果。</div> : null}
    {blocked ? <p className="ws-warning">该轨迹缺少业务结果，补齐后可启动分析。</p> : null}
    {running ? <progress className="diagnosis-progress" max={100} value={state?.progress ?? 0} aria-label="诊断进度"/> : null}
    <div className="trace-detail-tools">
      <div className="trace-detail-disclosures">
        <TraceContextPanel description={trace.data.task_description}/>
        {report ? <DiagnosisUsage report={report}/> : null}
        <TraceAnalysisHistory traceId={trace.id} params={historyParams} selectedId={job?.id}/>
        {disclosures}
      </div>
      <div className="ws-detail-status">
        {state && state.status !== 'completed' ? <span>{uiText(state.stage) || libraryDiagnosis[state.status]}
          {state.status === 'running' ? ' · ' + state.progress + '%' : ''}</span> : null}
        <div className="ws-actions">
          {actions}
          <a className="secondary small" href={playgroundLink(trace.id)}><Terminal size={14} aria-hidden="true"/>在 Playground 调试</a>
          <button className="secondary small" disabled={job?.status !== 'completed'}
            onClick={() => {if (job) setDialog({kind:'export', jobId:job.id})}}>
            <Download size={14} aria-hidden="true"/>导出诊断报告
          </button>
        </div>
      </div>
    </div>
  </>} trace={<ExecutionTrace key={trace.id} trace={trace} criticalStep={critical} selection={selection}/>}
    details={<Inspector key={job?.id ?? 'pending'} draft={draft} job={inspectorJob} report={report} onJump={jump}
      onReview={() => {if (job) setDialog({kind:'review', jobId:job.id})}} onFeedback={job && !historical ? feedback : undefined} busy={busy}
      onRegenerate={canRegenerate ? regenerate : undefined} onRegenerateFeedback={job && !historical ? regenerateFeedback : undefined}/>}/>
  {dialog?.kind === 'review' && job?.id === dialog.jobId && job.result?.attribution && !historical
    ? <ReviewDialog job={job} messages={trace.data.messages} onClose={close} onDone={() => {close();refresh();notify('复核已保存')}}/> : null}
  {dialog?.kind === 'export' && job?.id === dialog.jobId ? <ExportDialog job={job} onClose={close}/> : null}</>
}
