import {useState} from 'react'
import type {ReactNode} from 'react'
import {ChevronRight, FileText} from 'lucide-react'
import type {LucideIcon} from 'lucide-react'
import {Dialog} from './Dialogs'

export default function TraceInfoPanel({title, icon:Icon, children}: {
  title:string; icon:LucideIcon; children:ReactNode
}) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className="secondary trace-info-trigger" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <Icon size={16} aria-hidden="true"/>
      <span>{title}</span>
      <ChevronRight className="trace-info-chevron" size={14} aria-hidden="true"/>
    </button>
    {open ? <Dialog title={title} className="trace-info-panel" onClose={() => setOpen(false)}>
      <div className="trace-info-body" role="region" aria-label={`${title}内容`} tabIndex={0}>{children}</div>
    </Dialog> : null}
  </>
}

export function TraceContextPanel({description}: {description?:string|null}) {
  const hasDescription = !!description?.trim()
  return <TraceInfoPanel title="轨迹任务说明" icon={FileText}>
    <p className={hasDescription ? 'trace-context-description' : 'trace-context-description muted'}>
      {hasDescription ? description : '未提供任务说明。'}
    </p>
  </TraceInfoPanel>
}
