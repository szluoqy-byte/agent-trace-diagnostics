import type {EngineName, Evidence, EvidenceSupport, Report} from '../types'
import Citation from './Citation'

export const engineLabels = {trajdebug: 'TrajDebug', agentdebugx: 'AgentDebugX'}
const engines = ['trajdebug', 'agentdebugx'] as const
const comparisonLabels = {
  aligned: '关键步骤一致，两路均有原文引用',
  same_step: '关键步骤一致，原文证据有缺口',
  different: '关键步骤不同',
  unresolved: '至少一路未定位到关键步骤',
  incomplete: '一路诊断未完成',
  succeeded: '两路均已处理业务成功的轨迹',
}
const synthesisStatusLabels = {
  running: '正在综合两路诊断结果…',
  incomplete: '两路均完成后才会生成综合分析。可查看各引擎状态。',
  not_needed: '业务运行成功，无需评估失败归因。',
  insufficient: '两路均缺少可核验的根因原文，证据不足。',
  failed: '综合分析失败，可查看两路原始结论。',
}
const supportLabels: Record<EvidenceSupport, string> = {high:'高', medium:'中', low:'低', insufficient:'证据不足'}

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
      <h3>恢复建议</h3>
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

export default function DiagnosisComparison({report, onJump, onEngine, reviewWaived, reviewed}: {
  report: Report; onJump: (evidence: Evidence) => void; onEngine: (engine: EngineName) => void;
  reviewWaived: boolean; reviewed: boolean
}) {
  const comparison = report.comparison
  const synthesis = report.synthesis
  const aligned = comparison?.state === 'aligned'
  const diverged = comparison?.state === 'different'
  const finalStep = aligned ? report.attribution?.critical_step
    : synthesis?.status === 'completed' ? synthesis.critical_step : null
  const synthesisError = synthesis?.status === 'failed' && synthesis.failure
    ? synthesis.failure === 'http' ? `模型服务返回 HTTP ${synthesis.http_status}`
      : synthesis.failure === 'format' ? '模型未返回可解析的评估结果' : '模型连接失败或超时'
    : null
  return <div className="diagnosis-comparison">
    <div className="section-title"><h2>综合结论</h2>{reviewWaived ? <span className="status-tag green">免复核</span> : null}</div>
    {comparison ? <p className="diagnosis-note">{comparisonLabels[comparison.state]}</p> : null}
    {finalStep != null ? <button className="text-button" onClick={() => onJump({step:finalStep, quote:''})}>
      {aligned ? `一致关键步骤 #${finalStep}`
        : reviewed ? `模型候选步骤 #${finalStep}` : `暂定关键步骤 #${finalStep} · 待人工复核`}
    </button> : null}
    {synthesis?.status === 'completed' ? <>
      <div className="conclusion"><strong>{synthesis.summary}</strong></div>
      {finalStep == null && !reviewed ? <p className="muted">关键步骤待人工复核。</p> : null}
      <p className="muted">独立评估 · {synthesis.model} · {synthesis.calls} 次调用</p>
      {synthesis.evidence.filter(e => e.engine === 'trajectory').map((e, i) =>
        <Citation key={i} evidence={e} onJump={onJump}/>)}
    </> : <p className="muted">{synthesis ? synthesisStatusLabels[synthesis.status]
      : comparison ? '此历史记录提供两路原始结论。重新生成诊断可生成综合分析。' : '等待引擎诊断结果…'}
      {synthesisError ? ` ${synthesisError}。` : null}</p>}
    {engines.map(engine => {
      const run = report.engine_runs?.[engine]
      if (!run) return null
      const current = comparison?.primary === engine ? report : run.report
      const conclusion = current?.attribution
      const assessment = diverged && synthesis?.status === 'completed' ? synthesis.assessments?.[engine] : undefined
      return <section key={engine} className="instance-card">
        <div className="instance-top"><h3>{engineLabels[engine]}</h3>
          <span className="mini-meta">{assessment ? `候选置信度 · ${supportLabels[assessment.support]}`
            : run.status === 'completed' ? '已完成' : run.status === 'failed' ? '失败' : '运行中'}</span></div>
        {run.error ? <p>{run.error}</p> : null}
        {conclusion?.critical_step != null ? <button className="text-button" onClick={() => onJump({step:conclusion.critical_step, quote:''})}>
          关键步骤 #{conclusion.critical_step}</button> : null}
        {conclusion?.summary ? <p>{conclusion.summary}</p> : null}
        {assessment ? <p className="muted">{assessment.reason}</p> : null}
        {synthesis?.status === 'completed' ? synthesis.evidence.filter(e => e.engine === engine).map((e, i) =>
          <Citation key={i} evidence={e} onJump={onJump}/>) : null}
        <button className="text-button" onClick={() => onEngine(engine)}>查看{engineLabels[engine]}详情</button>
      </section>
    })}
  </div>
}
