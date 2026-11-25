import type {Evidence, Report} from '../types'
import Citation from './Citation'

export const comparisonLabels = {
  aligned: '定位步骤一致，两路均有原文引用',
  same_step: '定位步骤一致，原文证据有缺口',
  different: '定位步骤不同',
  unresolved: '至少一路未定位到关键步骤',
  incomplete: '有一路诊断未完成',
  succeeded: '两路均已处理业务成功的轨迹',
}

export function AgentDebugXDetails({report, jobId, onJump}: {
  report: Report; jobId?: string; onJump: (evidence: Evidence) => void
}) {
  const findings = report.engine_detection?.findings ?? []
  const suggestion = report.attribution?.fix_suggestions ?? []
  return <div className="engine-evidence">
    {report.root_cause_agent ? <p>根因 Agent：{report.root_cause_agent}</p> : null}
    <h3>原文证据</h3>
    {report.evidence?.length ? report.evidence.map((evidence, index) =>
      <Citation key={index} evidence={evidence} onJump={onJump}/>) : <p className="muted">暂无可展示的原文引用。</p>}
    <h3>恢复建议</h3>
    {suggestion.length ? <ul>{suggestion.map((line, index) => <li key={index}>{line}</li>)}</ul>
      : <p className="muted">本次诊断没有恢复建议。</p>}
    {report.warnings?.map(warning => <p className="diagnosis-note" key={warning}>{warning}</p>)}
    {findings.length ? <details><summary>检测发现（{findings.length}）</summary>
      <ul>{findings.map((item, index) => <li key={index}>
        <strong>{item.failure_mode?.mode_id || '诊断线索'}</strong>
        {item.step_index != null ? <button className="text-button" onClick={() => onJump({step:item.step_index, quote:''})}>步骤 #{item.step_index}</button> : null}
        {item.evidence?.map((text, position) => <p key={position}>{text}</p>)}
      </li>)}</ul>
    </details> : null}
    {jobId ? <a className="text-button" href={`/api/jobs/${jobId}/artifacts/agentdebugx`} download>下载 AgentDebugX 原始报告</a> : null}
  </div>
}

export default function DiagnosisComparison({report, jobId, onJump}: {
  report: Report; jobId?: string; onJump: (evidence: Evidence) => void
}) {
  const comparison = report.comparison
  return <div className="diagnosis-comparison">
    <h2>双路诊断对照</h2>
    {comparison ? <p className="diagnosis-note">{comparisonLabels[comparison.state]}。两路结果供人工复核。</p> : null}
    {(['trajdebug', 'agentdebugx'] as const).map(engine => {
      const run = report.engine_runs?.[engine]
      if (!run) return null
      const current = comparison?.primary === engine ? report : run.report
      const conclusion = current?.attribution
      const standardEvidence = current?.triggers
        .filter(trigger => trigger.step === conclusion?.critical_step)
        .flatMap(trigger => [trigger.commitment, trigger.reference]) ?? []
      return <section key={engine} className="instance-card">
        <div className="instance-top"><h3>{engine === 'trajdebug' ? '标准诊断' : 'AgentDebugX'}</h3>
          <span className="mini-meta">{run.status === 'completed' ? '已完成' : run.status === 'failed' ? '失败' : '运行中'}</span></div>
        {run.error ? <p>{run.error}</p> : null}
        {conclusion?.critical_step != null ? <button className="text-button" onClick={() => onJump({step:conclusion.critical_step, quote:''})}>
          关键步骤 #{conclusion.critical_step}</button> : null}
        {conclusion?.summary ? <p>{conclusion.summary}</p> : null}
        {engine === 'trajdebug' && current ? standardEvidence.map((item, index) =>
          <Citation key={index} evidence={item} views={current.compression_views} onJump={onJump}/>) : null}
        {engine === 'agentdebugx' && current ? <AgentDebugXDetails report={current} jobId={jobId} onJump={onJump}/> : null}
      </section>
    })}
  </div>
}
