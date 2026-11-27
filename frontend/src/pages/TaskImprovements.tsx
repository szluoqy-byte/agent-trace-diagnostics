import {useState} from 'react'
import {ArrowUpRight, Sparkles} from 'lucide-react'
import {dateLabel, post} from '../api'
import {tracePath, useResource, useWorkspace} from '../workspace'

type Status = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'insufficient'
type ReportSummary = {id:string; status:Status; model:string; created_at:string}
type Sample = {id:string; trace_id:string; title:string; step:number; quote:string}
type AgentGroup = {key:string; agent:{name:string; agent_id?:string}; count:number; samples:Sample[]}
type Suggestion = {priority:'high'|'medium'|'low'; problem:string; change:string; verification:string; evidence_ids:string[]; trace_count:number}
type GroupResult = {status:'completed'|'failed'; suggestions?:Suggestion[]; error?:string}
type Report = ReportSummary & {
  error?: string
  snapshot:{version:string; total:number; failed:number; included:number; exclusions:Record<string,number>; agents:AgentGroup[]}
  results:Record<string,GroupResult>
}
type ImprovementPage = {report:Report|null}

const statusLabels: Record<Status,string> = {
  queued:'排队中', running:'生成中', completed:'已生成', partial:'部分生成', failed:'生成失败', insufficient:'可用证据不足',
}
const priorities = {high:'高优先级', medium:'中优先级', low:'低优先级'}
const exclusions: Record<string,string> = {
  missing_diagnosis:'缺少可用诊断', rejected:'归因已驳回', pending_review:'待人工复核',
  missing_step:'缺少关键步骤', missing_evidence:'缺少关键步骤原文证据', unknown_agent:'轨迹的 Agent 未识别',
}
const active = (status?:Status) => status === 'queued' || status === 'running'
const polling = (data:ImprovementPage) => active(data.report?.status) ? 1800 : 0

export default function TaskImprovements({taskId, runId}: {taskId:string; runId:string}) {
  const {version, busy, action, refresh} = useWorkspace()
  const path = `/tasks/${taskId}/runs/${runId}/improvements`
  const {data, error, loading} = useResource<ImprovementPage>(path, version, polling)
  const [submissionError, setSubmissionError] = useState('')
  const report = data?.report
  const generating = active(report?.status)
  const legacyGrouping = report?.snapshot.version === 'agent-improvements-v1'

  async function generate() {
    setSubmissionError('')
    await action(() => post<{id:string}>(path), undefined, setSubmissionError)
  }

  function evidenceLink(sample: Sample) {
    const params = new URLSearchParams({tab:'improvements', evidence_step:String(sample.step)})
    return '#' + tracePath(taskId, sample.trace_id, params)
  }

  return <div className="task-improvements">
    <section className="ws-surface improvement-intro">
      <div><h2>任务改进建议</h2><p>按轨迹所属的 Agent 汇总可核验的失败证据，提出改进和回归验证建议。</p></div>
      <button type="button" className="primary" disabled={busy || generating || loading || !!error} onClick={() => void generate()}>
        <Sparkles size={15}/>{generating && report ? statusLabels[report.status] : report?.status === 'partial' || report?.status === 'failed' ? '重试生成' : report ? '重新生成' : '生成改进建议'}
      </button>
    </section>
    {error || submissionError ? <div role="alert" className="ws-actions"><p className="error-inline">{error || submissionError}</p>
      {error ? <button className="secondary small" onClick={refresh}>重新加载</button> : null}</div> : null}
    {loading ? <p role="status" className="muted">正在读取改进建议…</p> : null}
    {!report && data ? <section className="ws-surface empty"><h3>暂无任务改进建议</h3>
      <p>诊断完成后，可按 Agent 生成建议并核对代表轨迹。</p></section> : null}
    {report ? <>
      <section className="ws-surface improvement-coverage">
        <div className="improvement-stats"><strong>{statusLabels[report.status]}</strong>
          <span>共 {report.snapshot.total} 条轨迹</span><span>业务失败 {report.snapshot.failed} 条</span>
          <span>纳入 {report.snapshot.included} 条</span><span>{report.snapshot.agents.length} 个 Agent</span></div>
        <p className="muted">{dateLabel(report.created_at)}{report.model && report.status !== 'insufficient' ? ` · ${report.model}` : ''}</p>
        {Object.keys(report.snapshot.exclusions).length ? <ul className="improvement-exclusions">
          {Object.entries(report.snapshot.exclusions).map(([reason,count]) => <li key={reason}>{legacyGrouping && reason === 'unknown_agent' ? '关键步骤的 Agent 归属待确认' : exclusions[reason] ?? reason} {count} 条</li>)}
        </ul> : null}
        {legacyGrouping && !generating ? <p className="muted">此报告使用旧版 Agent 分组规则。重新生成可按轨迹所属的 Agent 分组。</p> : null}
        {report.status === 'insufficient' ? <p>可在诊断和复核完成后重新生成。建议需要关键步骤、原文证据和可识别的 Agent。</p> : null}
        {report.error ? <p className="error-inline" role="alert">{report.error}</p> : null}
      </section>
      {report.snapshot.agents.map(group => {
        const result = report.results[group.key]
        const byId = new Map(group.samples.map(sample => [sample.id,sample]))
        return <section className="ws-surface improvement-agent" key={group.key}>
          <header><h3 title={group.agent.agent_id}>{group.agent.name}</h3>
            <p>纳入 {group.count} 条失败轨迹 · 使用 {group.samples.length} 条代表证据</p></header>
          {!result ? <p className="muted">{active(report.status) ? '等待生成建议…' : '该 Agent 的建议尚未生成。'}</p> : null}
          {result?.error ? <p className="error-inline" role="alert">{result.error}</p> : null}
          {result?.status === 'completed' && !result.suggestions?.length ? <p className="muted">当前证据不足以提出具体改进。</p> : null}
          {result?.suggestions?.map((item,index) => <article className="improvement-item" key={index}>
            <div className="improvement-item-heading"><span className={`improvement-priority ${item.priority}`}>{priorities[item.priority]}</span>
              <h4>{item.problem}</h4><small>证据支持 {item.trace_count} 条轨迹</small></div>
            <dl><dt>建议改动</dt><dd>{item.change}</dd><dt>回归验证</dt><dd>{item.verification}</dd></dl>
            <details><summary>查看轨迹证据</summary><ul>{item.evidence_ids.map(id => {
              const sample = byId.get(id)
              return sample ? <li key={id}><a href={evidenceLink(sample)}>{sample.title} · 步骤 #{sample.step}<ArrowUpRight size={13}/></a>
                <blockquote>{sample.quote}</blockquote></li> : null
            })}</ul></details>
          </article>)}
        </section>
      })}
    </> : null}
  </div>
}
