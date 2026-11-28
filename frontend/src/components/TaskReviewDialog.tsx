import {useState} from 'react'
import {post} from '../api'
import {useWorkspace} from '../workspace'
import {Dialog} from './Dialogs'

const decisions = {
  confirmed: {label:'确认模型结论', detail:'采用各条轨迹当前的模型结论和关键步骤，更新现有人工复核状态。'},
  rejected: {label:'驳回诊断结论', detail:'将所选诊断标为已驳回，清空人工结论中的关键步骤。'},
  reset: {label:'恢复系统状态', detail:'撤销当前人工判定。多路一致的轨迹恢复为免复核，其余恢复为待复核。'},
} as const
type Decision = keyof typeof decisions
type ReviewResult = {updated:number; skipped:{trace_id:string; title:string; reason:string}[]}

export default function TaskReviewDialog({path, ids, onClose, onUpdated}: {
  path: string
  ids: string[]
  onClose: () => void
  onUpdated: (remainingIds: string[]) => void
}) {
  const {busy, action, notify} = useWorkspace()
  const [decision, setDecision] = useState<Decision>('confirmed')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<ReviewResult | null>(null)

  async function submit() {
    if (busy) return
    setError('')
    const response = await action(() => post<ReviewResult>(path, {trace_ids:ids, decision, notes}), undefined, setError)
    if (!response) return
    if (response.updated) onUpdated(response.skipped.map(item => item.trace_id))
    if (response.skipped.length) {
      setResult(response)
      return
    }
    onClose()
    notify(`已更新 ${response.updated} 条轨迹的复核状态`)
  }

  return <Dialog title="批量复核" onClose={onClose} busy={busy}>
    <form onSubmit={event => {event.preventDefault(); void submit()}}>
      <div className="modal-body review-form">
        {result ? <>
          <p role="status">已更新 {result.updated} 条轨迹，跳过 {result.skipped.length} 条。</p>
          <ul className="task-review-skipped" aria-label="跳过的轨迹">{result.skipped.map(item =>
            <li key={item.trace_id}><strong>{item.title}</strong><span>{item.reason}</span></li>)}</ul>
        </> : <>
          <p>将对已选的 {ids.length} 条轨迹应用同一复核操作，包括跨页选择。</p>
          <label>复核决定<select value={decision} disabled={busy} onChange={event => setDecision(event.target.value as Decision)}>
            {Object.entries(decisions).map(([value, {label}]) => <option value={value} key={value}>{label}</option>)}
          </select></label>
          <p className="muted">{decisions[decision].detail} 未完成诊断的轨迹会被跳过。</p>
          <label>复核说明（选填）<textarea value={notes} maxLength={5000} rows={2} disabled={busy}
            onChange={event => setNotes(event.target.value)}/></label>
        </>}
        {error ? <p className="error-inline" role="alert">{error}</p> : null}
      </div>
      <footer>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>{result ? '完成' : '取消'}</button>
        {result ? null : <button type="submit" className="primary" disabled={busy}>{busy ? '正在更新…' : '更新复核状态'}</button>}
      </footer>
    </form>
  </Dialog>
}
