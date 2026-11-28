import {useState} from 'react'
import {api} from '../api'
import {businessOutcomes} from '../traceLibrary'
import type {KnownOutcome,LibrarySelection} from '../traceLibrary'
import {useWorkspace} from '../workspace'
import {Dialog} from './Dialogs'

type Target = Pick<LibrarySelection,'id'|'title'|'outcome'>
interface OutcomeResult {updated_ids:string[]; skipped:{id:string; reason:string}[]}

export default function TraceOutcomeDialog({targets,batch=false,onClose,onSaved}: {
  targets:Target[]; batch?:boolean; onClose:()=>void; onSaved?:(ids:string[],outcome:KnownOutcome)=>void
}) {
  const {busy,action,notify} = useWorkspace()
  const [outcome,setOutcome] = useState<KnownOutcome|''>('')
  const [error,setError] = useState('')
  const [result,setResult] = useState<OutcomeResult|null>(null)
  const pending = targets.filter(trace => trace.outcome === 'unknown')
  const skippedTitles = result ? new Map(targets.map(trace=>[trace.id,trace.title])) : null
  const title = batch ? '批量设置业务结果' : '补充业务结果'

  async function save() {
    if (!outcome || busy || !pending.length || result) return
    const value = outcome
    setError('')
    const ids = pending.map(trace => trace.id)
    const saved = await action(async () => {
      if (batch) return api<OutcomeResult>('/trace-library/outcomes', {
        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ids,outcome:value}),
      })
      await api('/trace-library/' + encodeURIComponent(ids[0]) + '/outcome', {
        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({outcome:value}),
      })
      return {updated_ids:ids,skipped:[]}
    }, undefined, setError)
    if (!saved) return
    onSaved?.(saved.updated_ids,value)
    if (saved.skipped.length) {
      setResult(saved)
    } else {
      notify(batch ? '已设置 ' + saved.updated_ids.length + ' 条轨迹的业务结果' : '已补充业务结果')
      onClose()
    }
  }

  return <Dialog title={title} onClose={onClose} busy={busy}>
    <form onSubmit={event=>{event.preventDefault();void save()}}>
      <div className="modal-body review-form">
        {result ? <>
          <p role="status">已设置 {result.updated_ids.length} 条轨迹，跳过 {result.skipped.length} 条。</p>
          <ul className="task-review-skipped" aria-label="跳过的轨迹">
            {result.skipped.map(item=><li key={item.id}>
              <strong>{skippedTitles?.get(item.id) ?? item.id}</strong><span>{item.reason}</span>
            </li>)}
          </ul>
        </> : <>
          <p className="muted">{batch ? `将为所选轨迹中 ${pending.length} 条结果未知的记录设置业务结果。` : targets[0]?.title}</p>
          {batch && pending.length < targets.length ? <p className="muted">已填写结果的 {targets.length-pending.length} 条轨迹将保留原结果。</p> : null}
          <label>业务结果<select value={outcome} disabled={busy} onChange={event=>setOutcome(event.target.value as KnownOutcome)}>
            <option value="" disabled>请选择业务结果</option>
            <option value="failed">{businessOutcomes.failed}</option><option value="succeeded">{businessOutcomes.succeeded}</option>
          </select></label>
          {error ? <p className="error-inline" role="alert">{error}</p> : null}
        </>}
      </div>
      <footer>{result ? <button type="button" className="primary" onClick={onClose}>完成</button> : <>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>取消</button>
        <button type="submit" className="primary" disabled={!outcome || busy || !pending.length}>{busy ? '保存中…' : '保存'}</button>
      </>}</footer>
    </form>
  </Dialog>
}
