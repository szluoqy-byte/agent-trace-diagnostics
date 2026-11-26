import {useId} from 'react'
import type {DiagnosisLevel, DiagnosisMode} from '../types'
import {engineLabels} from './DiagnosisComparison'

const levels = ['fast', 'standard', 'deep'] as const
export const defaultDiagnosisLevel: DiagnosisLevel = 'standard'

const options: Record<DiagnosisLevel, {label:string; name:string; note:string; tip:string}> = {
  fast: {
    label: 'Fast', name: '快速诊断', note: '单路',
    tip: '低成本快速定位，适用于日常 Badcase 和批量轨迹诊断。',
  },
  standard: {
    label: 'Standard', name: '标准诊断', note: '双路 · 默认推荐',
    tip: '兼顾诊断深度和推理成本，适合常规问题排查。',
  },
  deep: {
    label: 'Deep', name: '深度诊断', note: '三路',
    tip: '从多个角度交叉验证，适合疑难问题和复杂长轨迹。',
  },
}

export function diagnosisLevelFor(mode?: DiagnosisMode): DiagnosisLevel {
  if (mode === 'fast' || mode === 'agentdebugx') return 'fast'
  if (mode === 'deep' || mode === 'dual_chief') return 'deep'
  return defaultDiagnosisLevel
}

export function diagnosisDepthLabel(mode: DiagnosisMode) {
  if (mode === 'trajdebug' || mode === 'chief') return `${engineLabels[mode]} 单路`
  if (mode === 'dual') return 'TrajDebug + AgentDebugX'
  const {label, name} = options[diagnosisLevelFor(mode)]
  return `${label} · ${name}`
}

export function supportsAutoFeedback(mode: DiagnosisMode) {
  return mode === 'deep' || mode === 'trajdebug' || mode === 'dual' || mode === 'dual_chief'
}

export default function DiagnosisDepthField({value, onChange, disabled}: {
  value: DiagnosisLevel; onChange: (value: DiagnosisLevel) => void; disabled?: boolean
}) {
  const groupId = useId()
  function renderOption(mode: DiagnosisLevel) {
    const {label, name, note, tip} = options[mode]
    const id = `${groupId}-${mode}`
    return <label className="diagnosis-depth-choice" key={mode}>
      <input type="radio" name={groupId} value={mode} checked={value === mode}
        aria-labelledby={`${id}-label`} aria-describedby={`${id}-note ${id}-tip`} onChange={() => onChange(mode)}/>
      <span className="diagnosis-depth-copy">
        <span id={`${id}-label`} className="diagnosis-depth-label"><strong>{label}</strong><span>{name}</span></span>
        <span id={`${id}-note`} className="diagnosis-depth-note">{note}</span>
        <span id={`${id}-tip`} className="diagnosis-depth-tip">{tip}</span>
      </span>
    </label>
  }
  return <fieldset className="diagnosis-depth-field" disabled={disabled}>
    <legend>诊断等级</legend>
    <div className="diagnosis-depth-options">{levels.map(renderOption)}</div>
  </fieldset>
}
