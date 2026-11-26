import {useId} from 'react'
import type {DiagnosisMode} from '../types'

const depths = ['trajdebug', 'dual', 'dual_chief'] as const
const standaloneModes = ['agentdebugx', 'chief'] as const

const options = {
  trajdebug: {
    label: '低', name: '标准诊断',
    tip: '适合批量初筛。定位错误实例和关键步骤，模型开销通常最低。',
  },
  dual: {
    label: '中', name: '双路综合',
    tip: '适合重点排查。两种引擎交叉验证，业务失败时生成综合结论。',
  },
  dual_chief: {
    label: '高', name: '双路综合 + CHIEF',
    tip: '适合复杂链路。在中档基础上增加 CHIEF 因果回溯供复核，预计耗时和用量最高。',
  },
  agentdebugx: {
    label: 'AgentDebugX', name: '',
    tip: '独立定位根因事件，给出原文证据和恢复建议。',
  },
  chief: {
    label: 'CHIEF', name: '',
    tip: '独立回溯关键失败步骤，提供候选与原文证据。',
  },
} satisfies Record<DiagnosisMode, {label:string; name:string; tip:string}>

export function diagnosisDepthLabel(mode: DiagnosisMode) {
  const {label, name} = options[mode]
  return name ? `${label} · ${name}` : label
}

export function supportsAutoFeedback(mode: DiagnosisMode) {
  return mode === 'trajdebug' || mode === 'dual' || mode === 'dual_chief'
}

export default function DiagnosisDepthField({value, onChange, disabled}: {
  value: DiagnosisMode; onChange: (value: DiagnosisMode) => void; disabled?: boolean
}) {
  const groupId = useId()
  function renderOption(mode: DiagnosisMode) {
    const {label, name, tip} = options[mode]
    const id = `${groupId}-${mode}`
    return <label className="diagnosis-depth-choice" key={mode}>
      <input type="radio" name={groupId} value={mode} checked={value === mode}
        aria-labelledby={`${id}-label`} aria-describedby={`${id}-tip`} onChange={() => onChange(mode)}/>
      <span className="diagnosis-depth-copy">
        <span id={`${id}-label`} className="diagnosis-depth-label"><strong>{label}</strong>{name ? <span>{name}</span> : null}</span>
        <span id={`${id}-tip`} className="diagnosis-depth-tip">{tip}</span>
      </span>
    </label>
  }
  return <fieldset className="diagnosis-depth-field" disabled={disabled}>
    <legend>诊断深度</legend>
    <div className="diagnosis-depth-options">{depths.map(renderOption)}</div>
    <details className="diagnosis-depth-custom" open={value === 'agentdebugx' || value === 'chief'}>
      <summary>单独运行引擎</summary>
      <div className="diagnosis-depth-options diagnosis-depth-standalone">{standaloneModes.map(renderOption)}</div>
    </details>
  </fieldset>
}
