import {ArrowUpRight, CircleCheck} from 'lucide-react'
import type {EngineName, Evidence, EvidenceSupport, Report, ReviewData} from '../types'
import Citation from './Citation'

export const engineLabels = {trajdebug: 'TrajDebug', agentdebugx: 'AgentDebugX', chief: 'CHIEF'}
export const engines = ['trajdebug', 'agentdebugx', 'chief'] as const
const synthesisStatusLabels = {
  running: '正在综合已完成的诊断结果…',
  incomplete: '至少两路完成后才会生成综合分析。可查看各引擎状态。',
  not_needed: '业务运行成功，无需评估失败归因。',
  insufficient: '缺少可核验的根因原文，证据不足。',
  failed: '综合分析失败，可查看各引擎的原始结论。',
}
export const unsettledComparison = new Set(['different', 'unresolved', 'incomplete'])
const supportLabels: Record<EvidenceSupport, string> = {high:'高', medium:'中', low:'低', insufficient:'证据不足'}
const synthesisFailureLabels = {
  connection: '模型连接失败或超时',
  format: '模型输出未通过 JSON 格式校验',
  empty: '模型未返回评估内容',
  length: '评估输出达到 token 上限并被截断',
  filtered: '模型拒绝返回评估内容或服务触发了内容过滤',
}

export function AgentDebugXDetails({report, jobId, onJump, section = 'all'}: {
  report: Report; jobId?: string; onJump: (evidence: Evidence) => void;
  section?: 'all' | 'evidence' | 'findings' | 'recovery'
}) {
  const findings = report.engine_detection?.findings ?? []
  const suggestions = report.attribution?.fix_suggestions ?? []
  return <div className="engine-evidence">
    {report.root_cause_agent ? <p>根因 Agent：{report.root_cause_agent}</p> : null}
    {section === 'all' || section === 'evidence' ? <>
      <h3>原文证据</h3>
      {report.evidence?.length ? report.evidence.map((evidence, index) =>
        <Citation key={index} evidence={evidence} onJump={onJump}/>) : <p className="muted">暂无可展示的原文引用。</p>}
      {report.warnings?.map(warning => <p className="diagnosis-note" key={warning}>{warning}</p>)}
    </> : null}
    {section === 'all' || section === 'recovery' ? <>
      <h3>恢复推演</h3>
      {suggestions.length ? <ul>{suggestions.map((line, index) => <li key={index}>{line}</li>)}</ul>
        : <p className="muted">本次诊断没有恢复建议。</p>}
    </> : null}
    {section === 'all' || section === 'findings' ? <>
      <h3>检测发现</h3>
      {findings.length ? <ul>{findings.map((item, index) => <li key={index}>
        <strong>{item.failure_mode?.mode_id || '诊断线索'}</strong>
        {item.step_index != null ? <button className="text-button" onClick={() => onJump({step:item.step_index, quote:''})}>步骤 #{item.step_index}</button> : null}
        {item.evidence?.map((text, position) => <p key={position}>{text}</p>)}
      </li>)}</ul> : <p className="muted">本次诊断没有检测发现。</p>}
    </> : null}
    {jobId ? <a className="text-button" href={`/api/jobs/${jobId}/artifacts/agentdebugx`} download>下载 AgentDebugX 原始报告</a> : null}
  </div>
}

export default function DiagnosisComparison({report, onJump, onEngine, reviewWaived, review}: {
  report: Report; onJump: (evidence: Evidence) => void; onEngine: (engine: EngineName) => void;
  reviewWaived: boolean; review?: ReviewData
}) {
  const comparison = report.comparison
  const synthesis = report.synthesis
  const sameStep = comparison?.state === 'same_step'
  const agreed = comparison?.state === 'aligned' || sameStep
  const included = comparison?.engines
  const modelStep = agreed ? report.attribution?.critical_step
    : synthesis?.status === 'completed' ? synthesis.critical_step : null
  const finalStep = review ? review.decision === 'rejected' ? null : review.critical_step : modelStep
  const stepLabel = review ? '人工复核关键步骤' : agreed ? '一致关键步骤' : '暂定关键步骤'
  const synthesisError = synthesis?.status === 'failed' && synthesis.failure
    ? synthesis.failure === 'http' ? `模型服务返回 HTTP ${synthesis.http_status}` : synthesisFailureLabels[synthesis.failure]
    : null
  return <div className="diagnosis-comparison">
    <section className="diagnosis-summary">
    <div className="section-title"><h2>综合结论</h2>
      {review ? <span className={review.decision === 'rejected' ? 'status-tag' : 'status-tag green'}>
        {review.decision === 'rejected' ? '已驳回' : '已复核'}
      </span> : reviewWaived ? <span className="status-tag green">免复核</span> : null}
    </div>
    {included && included.length >= 2 && synthesis?.status !== 'not_needed' ? <p className="muted">
      纳入综合评估 {included.length} 路{comparison?.state === 'incomplete' ? '，部分引擎未完成。' : ''}
    </p> : null}
    {finalStep != null ? <button type="button" className="synthesis-step" onClick={() => onJump({step:finalStep, quote:''})}>
      <span className="synthesis-step-main">
        <span className="synthesis-step-label">{stepLabel} <strong>#{finalStep}</strong></span>
        {!agreed && !review ? <span className="status-tag">待人工复核</span> : null}
      </span>
      <span className="synthesis-step-action">定位轨迹 <ArrowUpRight size={18} aria-hidden="true"/></span>
    </button> : null}
    <div className="diagnosis-summary-text">
    {synthesis?.status === 'completed' ? <>
      <div className="conclusion"><strong>{synthesis.summary}</strong></div>
      {finalStep == null && !review ? <p className="muted">关键步骤待人工复核。</p> : null}
    </> : agreed && modelStep != null ? <>
      <div className="conclusion"><strong>参与综合的各路均定位到该关键步骤。</strong></div>
      {synthesis?.status === 'failed' ? <p className="muted">综合说明生成失败，可查看引擎详情。
        {synthesisError ? ` ${synthesisError}。` : null}</p> : null}
    </> : <p className="muted">{synthesis ? synthesisStatusLabels[synthesis.status]
      : comparison ? '此历史记录可查看各引擎原始结论。重新生成诊断可生成综合分析。' : '等待引擎诊断结果…'}
      {synthesisError ? ` ${synthesisError}。` : null}</p>}
    {sameStep ? <p className="muted">部分引擎缺少可核验的原文引用，可点击关键步骤查看轨迹。</p> : null}
    {review ? <div className="review-note"><CircleCheck size={15}/><div>人工复核结论<p>{review.summary}</p></div></div> : null}
    </div>
    </section>
    <div className="inspector-scroll diagnosis-candidates" role="region" aria-label="引擎结论" tabIndex={0}>
    {engines.map(engine => {
      const run = report.engine_runs?.[engine]
      if (!run) return null
      const current = comparison?.primary === engine ? report : run.report
      const conclusion = current?.attribution
      const step = conclusion?.critical_step
      const assessment = synthesis?.status === 'completed' ? synthesis.assessments?.[engine] : undefined
      const legacyReference = engine === 'chief' && !included && comparison && run.status === 'completed'
      return <section key={engine} className="instance-card">
        <div className="instance-top"><h3>{engineLabels[engine]}</h3>
          {legacyReference ? <span className="mini-meta">历史复核参考</span> : null}
          {!assessment ? <span className="mini-meta">{run.status === 'completed' ? '已完成' : run.status === 'failed' ? '失败' : '运行中'}</span> : null}
        </div>
        {step != null || assessment ? <div className="engine-candidate">
          {step != null ? <button type="button" className="engine-candidate-step"
            onClick={() => onJump({step, quote:''})}>关键步骤 <strong>#{step}</strong>
            <ArrowUpRight size={14} aria-hidden="true"/>
          </button> : null}
          {assessment ? <span className="engine-candidate-confidence">候选置信度 <strong>{supportLabels[assessment.support]}</strong></span> : null}
        </div> : null}
        {run.error || conclusion?.summary ? <p>{run.error || conclusion?.summary}</p> : null}
        <button type="button" className="text-button" onClick={() => onEngine(engine)}>查看{engineLabels[engine]}详情</button>
      </section>
    })}
    </div>
  </div>
}
