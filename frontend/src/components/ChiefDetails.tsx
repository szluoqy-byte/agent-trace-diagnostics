import type {Evidence, Report} from '../types'
import Citation from './Citation'

interface ChiefOutput {
  dag_graph?: {
    subtasks: {
      id: string
      name: string
      step_range: string
      oracle: string
      agents: {agent: string}[]
      data_flow: {from_step: number; to_step: number; data_item: string}[]
    }[]
    subtask_edges: {from: string; to: string; explanation: string}[]
    agent_edges: {
      'The Subtask Name': string
      Agents_edges: {From_agent: string; To_agent: string; agent_explanation: string}[]
    }[]
  }
  candidates?: {
    candidate_error_steps: {
      step_id: number
      agent_in_step: string[]
      confidence?: number | null
      irrecoverability_issue: {reason: string}
      data_issue: {explanation: string}
    }[]
  }
}

export default function ChiefDetails({report, jobId, onJump, section}: {
  report: Report; jobId?: string; onJump: (evidence: Evidence) => void; section: 'evidence' | 'graph'
}) {
  const output = report.official_output as ChiefOutput | undefined
  const graph = output?.dag_graph
  const candidates = output?.candidates?.candidate_error_steps ?? []
  return <div className="engine-evidence">
    {section === 'evidence' ? <>
      <h3>关键步骤原文</h3>
      {report.evidence?.length ? report.evidence.map((evidence, index) =>
        <Citation key={index} evidence={evidence} onJump={onJump}/>) : <p className="muted">暂无可展示的原文引用。</p>}
      <h3>候选失败步骤</h3>
      {candidates.length ? <ul>{candidates.map((item, index) =>
        <li key={index}>
          <button type="button" className="text-button" onClick={() => onJump({step:item.step_id, quote:''})}>步骤 #{item.step_id}</button>
          {item.agent_in_step.length ? <span> · {item.agent_in_step.join('、')}</span> : null}
          {typeof item.confidence === 'number' && item.confidence >= 0 && item.confidence <= 1
            ? <span className="mini-meta" title={`CHIEF 原始评分 ${item.confidence}`}> · CHIEF 候选置信度 {item.confidence >= 0.8 ? '高' : item.confidence >= 0.5 ? '中' : '低'}</span> : null}
          {item.irrecoverability_issue.reason || item.data_issue.explanation ?
            <p>{item.irrecoverability_issue.reason || item.data_issue.explanation}</p> : null}
        </li>
      )}</ul> : <p className="muted">暂无候选步骤。</p>}
    </> : <>
      <h3>因果回溯</h3>
      {graph ? <>
        <p className="muted">以下依赖和数据流由 CHIEF 根据轨迹推断，请结合原文核验。</p>
        {graph.subtask_edges.length ? <ul>{graph.subtask_edges.map((edge, index) =>
          <li key={index}><strong>{edge.from} → {edge.to}</strong> {edge.explanation}</li>
        )}</ul> : null}
        {graph.subtasks.map(task => <details className="instance-card" key={task.id}>
          <summary>{task.id} · {task.name} · 步骤 {task.step_range}</summary>
          {task.oracle ? <p>成功条件 · {task.oracle}</p> : null}
          {task.agents.length ? <p>Agent · {task.agents.map(item => item.agent).join('、')}</p> : null}
          {task.data_flow.length ? <ul>{task.data_flow.map((flow, index) =>
            <li key={index}>#{flow.from_step} → #{flow.to_step} · {flow.data_item}</li>
          )}</ul> : null}
          {graph.agent_edges.filter(item => item['The Subtask Name'] === task.name).flatMap(item => item.Agents_edges)
            .map((edge, index) => <p key={index}>{edge.From_agent} → {edge.To_agent} · {edge.agent_explanation}</p>)}
        </details>)}
      </> : <p className="muted">本次诊断没有因果图。</p>}
    </>}
    {jobId ? <a className="text-button" href={`/api/jobs/${jobId}/artifacts/chief`} download>下载 CHIEF 原始报告</a> : null}
  </div>
}
