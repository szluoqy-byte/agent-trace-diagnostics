import {useState} from 'react'
import {Trash2} from 'lucide-react'
import {api, post} from '../api'
import {useWorkspace} from '../workspace'
import {Dialog} from './Dialogs'

type RecordKind = 'diagnosis' | 'failure_patterns' | 'trace'

const descriptions: Record<RecordKind, string> = {
  diagnosis: '该任务的分析记录、改进建议和复核结果会一并删除。任务使用的轨迹仍保留在轨迹库。',
  failure_patterns: '该任务的分析结果和复核记录会一并删除。轨迹和来源诊断仍保留。',
  trace: '原始轨迹、独立诊断、改进建议和复核结果会一并删除。',
}

export default function DeleteRecord({id, name, kind, compact = false, blocked, onDeleted}: {
  id: string
  name: string
  kind: RecordKind
  compact?: boolean
  blocked?: string
  onDeleted?: () => void
}) {
  const {action, busy} = useWorkspace()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const title = kind === 'trace' ? '删除轨迹' : '删除任务'

  async function remove() {
    if (busy || blocked) return
    setError('')
    const path = kind === 'trace' ? '/traces/' : '/tasks/'
    const result = await action(
      () => api<{deleted: boolean}>(path + encodeURIComponent(id), {method:'DELETE'}),
      kind === 'trace' ? '轨迹已删除' : '任务已删除',
      setError,
    )
    if (result?.deleted) {
      setOpen(false)
      onDeleted?.()
    }
  }

  return <>
    <button type="button" className={compact ? 'text-button ws-delete-link' : 'secondary ws-delete-link'}
      aria-label={`${title} ${name}`} onClick={() => {setError(''); setOpen(true)}}>
      {compact ? '删除' : <><Trash2 size={15}/>{title}</>}
    </button>
    {open ? <Dialog title={title} onClose={() => setOpen(false)} busy={busy}>
      <div className="modal-body ws-delete-body">
        {blocked ? <><strong>{name}</strong><p>{blocked}</p></> : <>
          <p>确定删除「{name}」吗？</p>
          <p className="muted">{descriptions[kind]} 删除后无法恢复。</p>
        </>}
        {error ? <p className="error-inline" role="alert">{error}</p> : null}
      </div>
      <footer>
        <button type="button" className="secondary" disabled={busy} onClick={() => setOpen(false)}>{blocked ? '关闭' : '取消'}</button>
        {blocked ? null : <button type="button" className="primary ws-delete-confirm" disabled={busy} onClick={() => void remove()}>
          {busy ? '正在删除…' : '确认删除'}
        </button>}
      </footer>
    </Dialog> : null}
  </>
}

export function traceDeletionBlock(taskCount: number, active: boolean) {
  if (taskCount) return '此轨迹仍被分析任务引用，请先删除关联任务。'
  return active ? '仍有诊断在排队或运行，请处理完成后再删除。' : undefined
}

type DeleteTarget = {id:string; name:string}
type DeletionResult = {deleted_ids:string[]; skipped:{id:string; reason:string}[]}

export function DeleteRecords({records, kind, onDeleted, disabled}: {
  records: DeleteTarget[]
  kind: 'task' | 'trace'
  onDeleted: (ids: string[]) => void
  disabled?: boolean
}) {
  const {action, busy, notify} = useWorkspace()
  const [targets, setTargets] = useState<DeleteTarget[] | null>(null)
  const [result, setResult] = useState<DeletionResult | null>(null)
  const [error, setError] = useState('')
  const isTrace = kind === 'trace'
  const entity = isTrace ? '轨迹' : '任务'
  const unit = isTrace ? '条' : '个'
  const title = `批量删除${entity}`

  async function remove() {
    if (!targets || busy) return
    setError('')
    const response = await action(() => post<DeletionResult>(`/${isTrace ? 'traces' : 'tasks'}/batch-delete`, {
      ids: targets.map(record => record.id),
    }), undefined, setError)
    if (!response) return
    if (response.deleted_ids.length) {
      onDeleted(response.deleted_ids)
      notify(`已删除 ${response.deleted_ids.length} ${unit}${entity}`)
    }
    if (response.skipped.length) setResult(response)
    else setTargets(null)
  }

  return <>
    <button type="button" className="secondary small ws-delete-link" disabled={busy || disabled || !records.length}
      onClick={() => {setTargets(records);setResult(null);setError('')}}><Trash2 size={14}/>{title}</button>
    {targets ? <Dialog title={title} onClose={() => setTargets(null)} busy={busy}>
      <div className="modal-body ws-delete-body">
        {result ? <>
          <p>已删除 {result.deleted_ids.length} {unit}{entity}，{result.skipped.length} {unit}未删除。</p>
          <ul className="ws-delete-skipped">{result.skipped.map(({id,reason}) => <li key={id}>
            <strong>{targets.find(record => record.id === id)?.name ?? id}</strong><span>{reason}</span>
          </li>)}</ul>
        </> : <>
          <p>确定删除已选的 {targets.length} {unit}{entity}吗？</p>
          <p className="muted">{isTrace ? descriptions.trace : '所选任务的分析结果、改进建议和复核记录会一并删除。任务使用的轨迹仍保留在轨迹库。'} 删除后无法恢复。</p>
          <p className="muted">有引用关系或仍在运行的记录会保留，并显示未删除原因。</p>
        </>}
        {error ? <p className="error-inline" role="alert">{error}</p> : null}
      </div>
      <footer>
        <button type="button" className="secondary" disabled={busy} onClick={() => setTargets(null)}>{result ? '关闭' : '取消'}</button>
        {result ? null : <button type="button" className="primary ws-delete-confirm" disabled={busy} onClick={() => void remove()}>
          {busy ? '正在删除…' : '确认删除'}
        </button>}
      </footer>
    </Dialog> : null}
  </>
}
