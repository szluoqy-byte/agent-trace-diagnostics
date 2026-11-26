import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { X, Download } from 'lucide-react'
import { post } from '../api'
import type { Job, Message, ReviewData } from '../types'
import {unsettledComparison} from './DiagnosisComparison'

export function Dialog({title, onClose, children, busy=false}: {title:string; onClose:()=>void; children:ReactNode; busy?:boolean}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    if(typeof dialog.showModal==='function')dialog.showModal()
    else dialog.setAttribute('open','')
    document.body.style.overflow = 'hidden'
    return () => {
      if(typeof dialog.close==='function')dialog.close()
      else dialog.removeAttribute('open')
      document.body.style.overflow = overflow
      previous?.focus({preventScroll:true})
    }
  }, [])
  return <dialog ref={ref} aria-label={title} className="modal ws-dialog"
    onCancel={event=>{event.preventDefault();if(!busy)onClose()}}>
    <header><h2>{title}</h2><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={19}/></button></header>
    {children}
  </dialog>
}

export function ReviewDialog({job, messages, onClose, onDone}: {job:Job; messages?:Message[]; onClose:()=>void; onDone:()=>void}) {
  const report = job.result!
  const synthesis = report.synthesis?.status === 'completed' ? report.synthesis : undefined
  const preferred = synthesis?.preferred_engine
  const source = preferred && preferred !== report.comparison?.primary ? report.engine_runs?.[preferred]?.report : report
  const original = synthesis ? {
    critical_step:synthesis.critical_step, summary:synthesis.summary, category:preferred ? source?.attribution?.category ?? '' : '',
  } : report.task_outcome !== 'succeeded' && unsettledComparison.has(report.comparison?.state ?? '') ? {
    critical_step:null, category:'', summary:'各路定位尚未形成一致结论，关键步骤待人工复核。',
  } : report.attribution!
  const saved = job.reviews?.[0]?.data
  const initial = saved ?? original
  const [value, setValue] = useState<ReviewData>({
    decision:saved?.decision ?? 'confirmed', critical_step:initial.critical_step,
    category:initial.category, summary:initial.summary, notes:saved?.notes ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const editable = value.decision !== 'confirmed'
  const canChooseStep = value.decision === 'modified'
  const freeStepChoice = source?.algorithm === 'AgentDebugX' || source?.algorithm === 'CHIEF'
  const steps = messages?.filter(message => freeStepChoice || message.role !== 'user' && message.judgable !== false)
  function chooseDecision(decision:string) {
    setError('')
    setValue(old=>{
      if(decision === 'confirmed') {
        return {...old, decision, critical_step:original.critical_step, category:original.category, summary:original.summary}
      }
      return {...old, decision, critical_step:decision==='rejected'?null:old.critical_step}
    })
  }
  async function save() {
    if(busy||!value.summary.trim())return
    setBusy(true)
    setError('')
    try {
      await post(`/jobs/${job.id}/reviews`,value)
      onDone()
    } catch(e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return <Dialog title="人工复核" onClose={onClose} busy={busy}>
    <form onSubmit={event=>{event.preventDefault();void save()}}>
      <div className="modal-body review-form">
        <label>复核决定<select value={value.decision} disabled={busy} onChange={event=>chooseDecision(event.target.value)}>
          <option value="confirmed">确认模型结论</option><option value="modified">修改结论</option><option value="rejected">驳回结论</option>
        </select></label>
        <p className="muted">{value.decision==='confirmed'?'确认后保留模型的关键步骤、分类和结论。可以补充复核说明。':value.decision==='rejected'?'驳回会清空关键步骤。可在结论和说明中记录判断依据。':'人工结论单独保存，可以随时查看模型原始结论。'}</p>
        <div className="form-row">
          <label>关键步骤（可留空）
            {steps && canChooseStep
              ? <select value={value.critical_step??''} disabled={busy} onChange={event=>setValue({...value,critical_step:event.target.value===''?null:Number(event.target.value)})}>
                  <option value="">未确定</option>{steps.map(step=><option key={step.step} value={step.step}>#{step.step} · {step.role}{step.name?' · '+step.name:''}</option>)}
                </select>
              : <input type="number" min="0" step="1" readOnly={!canChooseStep} disabled={busy} value={value.critical_step??''}
                  onChange={event=>setValue({...value,critical_step:event.target.value===''?null:Number(event.target.value)})}/>}
          </label>
          <label>根因类别<input maxLength={200} readOnly={!editable} disabled={busy} value={value.category} onChange={event=>setValue({...value,category:event.target.value})}/></label>
        </div>
        <label>诊断结论<textarea required maxLength={5000} rows={4} readOnly={!editable} disabled={busy} value={value.summary} onChange={event=>setValue({...value,summary:event.target.value})}/></label>
        <label>复核说明<textarea maxLength={5000} rows={3} disabled={busy} placeholder="记录判断依据、待补充证据或后续验证计划" value={value.notes} onChange={event=>setValue({...value,notes:event.target.value})}/></label>
        {error?<p className="error-inline" role="alert">{error}</p>:null}
      </div>
      <footer><button type="button" className="secondary" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy||!value.summary.trim()}>{busy?'保存中…':'保存复核'}</button></footer>
    </form>
  </Dialog>
}

export function ExportDialog({job, onClose}: {job: Job; onClose:()=>void}) {
  return <Dialog title="导出诊断报告" onClose={onClose}><div className="modal-body"><p className="muted">选择导出格式。完整 JSON 包含轨迹内容和已有的诊断、建议及复核记录。</p><div className="export-options"><a className="secondary" href={`/api/jobs/${job.id}/export?format=md`} download><Download size={18}/>Markdown 报告</a><a className="secondary" href={`/api/jobs/${job.id}/export?format=json`} download><Download size={18}/>完整 JSON 记录</a>{job.result?.provenance?.run_id ? <a className="secondary" href={`/api/jobs/${job.id}/artifacts/final`} download><Download size={18}/>诊断原始结果</a> : null}{job.feedback?.status === 'completed' ? <a className="secondary" href={`/api/jobs/${job.id}/feedback/export`} download><Download size={18}/>改进建议 JSON</a> : null}</div></div></Dialog>
}
