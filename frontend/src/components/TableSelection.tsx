import {useEffect, useRef, useState} from 'react'
import {api} from '../api'

export default function TableSelection({pageIds, selectedIds, total, onPage, onAll, disabled, loading}: {
  pageIds: string[]
  selectedIds: ReadonlySet<string>
  total: number
  onPage: (checked: boolean) => void
  onAll: (checked: boolean) => void
  disabled?: boolean
  loading?: boolean
}) {
  const pageCount = pageIds.filter(id => selectedIds.has(id)).length
  return <div className="ws-selection-scopes" role="group" aria-label="选择范围" aria-busy={loading || undefined}>
    <SelectionCheckbox label="全选本页" count={pageCount} total={pageIds.length} disabled={disabled} onChange={onPage}/>
    <SelectionCheckbox label="跨页全选" description="选择当前筛选结果的所有记录" count={selectedIds.size} total={total} disabled={disabled} onChange={onAll}/>
  </div>
}

function SelectionCheckbox({label, description, count, total, disabled, onChange}: {
  label: string; description?: string; count: number; total: number; disabled?: boolean; onChange: (checked: boolean) => void
}) {
  const checked = total > 0 && count === total
  return <label title={description}><input type="checkbox" checked={checked} disabled={disabled || !total} aria-description={description}
    ref={element => {if (element) element.indeterminate = count > 0 && !checked}}
    onChange={event => onChange(event.target.checked)}/>{label}</label>
}

export function useSelectionRequest(path: string) {
  const pending = useRef<AbortController | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<{path:string; message:string} | null>(null)
  useEffect(() => () => pending.current?.abort(), [path])

  async function load<T>(onLoaded: (items: T[]) => void) {
    if (pending.current && !pending.current.signal.aborted) return
    const controller = new AbortController()
    pending.current = controller
    setLoading(true)
    setFailure(null)
    try {
      const {items} = await api<{items:T[]}>(path, {signal:controller.signal})
      if (!controller.signal.aborted) onLoaded(items)
    } catch (error) {
      if (!controller.signal.aborted) setFailure({path,message:(error as Error).message})
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setLoading(false)
      }
    }
  }

  return {load, loading, error:failure?.path === path ? failure.message : ''}
}
