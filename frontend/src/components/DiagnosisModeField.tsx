import type {DiagnosisMode} from '../types'

export const diagnosisModeLabels: Record<DiagnosisMode, string> = {
  trajdebug: '标准诊断',
  agentdebugx: 'AgentDebugX',
  dual: '双路诊断',
}

const descriptions: Record<DiagnosisMode, string> = {
  trajdebug: '执行五阶段诊断，查看错误实例、修复状态和关键失败步骤。',
  agentdebugx: '回查根因事件，给出原文证据和恢复建议。',
  dual: '并行运行两种诊断。业务失败且两路均完成时，调用模型进行综合评估。',
}

export default function DiagnosisModeField({value, onChange, disabled}: {
  value: DiagnosisMode; onChange: (value: DiagnosisMode) => void; disabled?: boolean
}) {
  return <div className="diagnosis-mode-field">
    <label>诊断方式<select value={value} onChange={event => onChange(event.target.value as DiagnosisMode)} disabled={disabled}>
      {Object.entries(diagnosisModeLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
    </select></label>
    <p className="ws-help">{descriptions[value]}</p>
  </div>
}
