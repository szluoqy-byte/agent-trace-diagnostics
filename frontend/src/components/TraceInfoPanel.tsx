import {useState} from 'react'
import type {ReactNode} from 'react'
import {ChevronRight, FileText} from 'lucide-react'
import type {LucideIcon} from 'lucide-react'
import {sourceLabel} from '../branding'
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

export function TraceContextPanel({description, source}: {description?:string|null; source?:string|null}) {
  const hasDescription = !!description?.trim()
  const origin = sourceLabel(source?.trim() ?? '')
  return <TraceInfoPanel title="轨迹任务说明及来源" icon={FileText}>
    <dl className="trace-context-source">
      <dt>轨迹来源</dt>
      <dd>{origin || '未记录来源'}</dd>
    </dl>
    <section className="trace-context-description">
      <h3>任务说明</h3>
      <p className={hasDescription ? undefined : 'muted'}>{hasDescription ? description : '未提供任务说明。'}</p>
    </section>
  </TraceInfoPanel>
}
