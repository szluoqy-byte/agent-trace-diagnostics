import type {ModelUsage, Report} from '../types'

const engines = [{id:'trajdebug',label:'TrajDebug'}, {id:'agentdebugx',label:'AgentDebugX'}] as const
const integers = new Intl.NumberFormat('zh-CN')
const decimals = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2})

interface UsageRow {
  label: string
  seconds?: number
  usage?: Partial<ModelUsage>
}

function elapsed(report?: Report | null) {
  if (report?.duration_seconds != null) return report.duration_seconds
  const stages = report?.stage_metrics
  return stages?.length && stages.every(stage => Number.isFinite(stage.seconds))
    ? stages.reduce((total, stage) => total + stage.seconds, 0) : undefined
}

function modelUsage(report?: Report | null): Partial<ModelUsage> | undefined {
  if (report?.usage) return report.usage
  const stages = report?.stage_metrics
  if (!stages?.length) return undefined
  const sum = (key: 'requests' | 'prompt_tokens' | 'completion_tokens') =>
    stages.every(stage => typeof stage[key] === 'number') ? stages.reduce((total, stage) => total + stage[key], 0) : undefined
  return {requests:sum('requests'), prompt_tokens:sum('prompt_tokens'), completion_tokens:sum('completion_tokens')}
}

function totalTokens(usage?: Partial<ModelUsage>) {
  if (usage?.prompt_tokens == null || usage.completion_tokens == null) return undefined
  return usage.prompt_tokens + usage.completion_tokens
}

export default function DiagnosisUsage({report}: {report:Report}) {
  const rows: UsageRow[] = []
  if (report.engine_runs) {
    for (const {id,label} of engines) {
      const run = report.engine_runs[id]
      if (!run) continue
      const result = report.comparison?.primary === id ? report : run.report
      rows.push({label, seconds:run.seconds ?? elapsed(result), usage:modelUsage(result) ?? run.usage})
    }
  } else if (report.usage || report.stage_metrics?.length) {
    rows.push({label:report.algorithm || report.engine_version || '诊断', seconds:elapsed(report), usage:modelUsage(report)})
  }
  const synthesis = report.synthesis
  if (synthesis?.status === 'completed' || (synthesis?.status === 'failed' && synthesis.duration_seconds != null)) {
    rows.push({
      label:'综合分析', seconds:synthesis.duration_seconds,
      usage:{requests:synthesis.requests ?? (synthesis.status === 'completed' ? synthesis.calls : undefined),
        request_unit:synthesis.requests == null ? 'completion_calls' : undefined,
        prompt_tokens:synthesis.prompt_tokens, completion_tokens:synthesis.completion_tokens},
    })
  }
  if (!rows.length || report.mode === 'demo') return null

  return <details className="diagnosis-usage">
    <summary>算法用量</summary>
    <table aria-label="算法用量">
      <thead><tr><th scope="col">算法</th><th scope="col">请求 / 调用</th><th scope="col">耗时</th><th scope="col">Tokens</th></tr></thead>
      <tbody>{rows.map(({label,seconds,usage}) => {
        const tokens = totalTokens(usage)
        return <tr key={label}>
          <th scope="row">{label}</th>
          <td>{usage?.requests == null ? '—' : `${integers.format(usage.requests)} 次${usage.request_unit === 'completion_calls' || label === 'AgentDebugX' ? '调用' : ''}`}</td>
          <td>{seconds == null ? '—' : `${decimals.format(seconds)} 秒`}</td>
          <td>{tokens == null ? '—' : integers.format(tokens)}</td>
        </tr>
      })}</tbody>
    </table>
  </details>
}
