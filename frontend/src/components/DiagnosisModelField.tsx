import type {Config} from '../types'
import {useResource, useWorkspace} from '../workspace'

interface DiagnosisModel {
  id: string
  name: string
  model: string
  is_default: boolean
  runtime: Pick<Config, 'max_retries'|'timeout'|'max_tokens'|'cluster_json_mode'>
}

export function useDiagnosisModels(preferredId: string, enabled = true) {
  const {version} = useWorkspace()
  const {data, loading, error} = useResource<{items:DiagnosisModel[]}>(
    enabled ? '/model-configurations' : null, version,
  )
  const items = data?.items ?? []
  const selected = items.find(item => preferredId ? item.id === preferredId : item.is_default)
  return {items, selected, loading, error, missing: !!data && !!preferredId && !selected}
}

export default function DiagnosisModelField({options, onChange, disabled}: {
  options: ReturnType<typeof useDiagnosisModels>
  onChange: (id:string) => void
  disabled?: boolean
}) {
  const {openSettings, refresh} = useWorkspace()
  const {items, selected, loading, error, missing} = options

  return <>
    <label className="diagnosis-model-field">诊断模型
      <select value={selected?.id ?? ''} onChange={event => onChange(event.target.value)}
        disabled={disabled || loading || !!error || !items.length}>
        <option value="" disabled>{loading ? '正在加载模型…' : '请选择诊断模型'}</option>
        {items.map(item => <option key={item.id} value={item.id}>
          {item.name} · {item.model}{item.is_default ? '（默认）' : ''}
        </option>)}
      </select>
    </label>
    {error ? <p className="error-inline" role="alert">{error} <button type="button" className="text-button" onClick={refresh}>重新加载</button></p>
      : !loading && !items.length ? <p className="ws-help">尚未配置诊断模型。 <button type="button" className="text-button" onClick={openSettings}>查看设置</button></p>
      : missing ? <p className="ws-help" role="status">原选模型不可用，请重新选择。</p> : null}
  </>
}
