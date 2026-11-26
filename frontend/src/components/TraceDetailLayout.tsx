import {useEffect,useId,useState} from 'react'
import type {ReactNode} from 'react'
import type {Evidence} from '../types'
import Tabs from './Tabs'

export default function TraceDetailLayout({header,trace,details,detailsLabel,selection}: {
  header:ReactNode; trace:ReactNode; details:ReactNode; detailsLabel:string; selection?:Evidence|null
}) {
  const [active,setActive]=useState('trace')
  const id=useId()
  useEffect(()=>{
    if(selection?.step!=null)setActive('trace')
  },[selection])

  return <div className="trace-detail-screen">
    <header className="trace-detail-header">{header}</header>
    <Tabs id={id} label="轨迹详情视图" className="ws-tabs trace-detail-switcher"
      items={[{id:'trace',label:'执行轨迹'},{id:'details',label:detailsLabel}]} active={active} onChange={setActive}/>
    <div className="trace-detail-panes">
      <div className="trace-detail-pane" id={`${id}-panel-trace`} role="tabpanel"
        aria-labelledby={`${id}-tab-trace`} data-active={active==='trace'||undefined}>{trace}</div>
      <div className="trace-detail-pane" id={`${id}-panel-details`} role="tabpanel"
        aria-labelledby={`${id}-tab-details`} data-active={active==='details'||undefined}>{details}</div>
    </div>
  </div>
}
