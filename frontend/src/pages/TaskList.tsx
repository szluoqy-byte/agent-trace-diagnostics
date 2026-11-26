import {useLayoutEffect, useRef, useState} from 'react'
import {Plus, Copy} from 'lucide-react'
import DeleteRecord, {DeleteRecords} from '../components/DeleteRecord'
import Pagination from '../components/Pagination'
import TableSelection from '../components/TableSelection'
import {patternTaskPath} from '../patterns'
import {dateLabel} from '../api'
import {Badge,useResource,useWorkspace} from '../workspace'
import type {AnalysisTask} from '../workspace'

let lastFilters={search:'',status:'all',kind:'all'}

export default function TaskList(){
  const {version,refresh,busy}=useWorkspace(),{data:tasks,error}=useResource<AnalysisTask[]>('/tasks?kind=all',version,2500)
  const [search,setSearch]=useState(lastFilters.search),[status,setStatus]=useState(lastFilters.status),[kind,setKind]=useState(lastFilters.kind)
  const [requestedPage,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
  const [selected,setSelected]=useState<ReadonlySet<string>>(new Set())
  function updateFilters(next:Partial<typeof lastFilters>){lastFilters={...lastFilters,...next};setSearch(lastFilters.search);setStatus(lastFilters.status);setKind(lastFilters.kind);setPage(1);setSelected(new Set())}
  const filtered=(tasks??[]) .filter(t=>(kind==='all'||(t.kind??'diagnosis')===kind)&&(`${t.name} ${t.agent} ${t.version}`.toLowerCase().includes(search.trim().toLowerCase()))&&(status==='all'||status==='attention'&&(t.status==='partial'||t.status==='pending'||t.counts.failed>0||t.counts.feedback_failed>0||t.counts.review_pending>0)||status===t.status))
  const selectedTasks=filtered.filter(task=>selected.has(task.id))
  const selectedIds=new Set(selectedTasks.map(task=>task.id))
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize)),page=Math.min(requestedPage,pages)
  const currentPage=filtered.slice((page-1)*pageSize,page*pageSize)
  const tableViewport=useRef<HTMLDivElement>(null)
  useLayoutEffect(()=>{if(tableViewport.current)tableViewport.current.scrollTop=0},[page,pageSize,search,status,kind])

  function selectTasks(ids:string[], checked:boolean){
    setSelected(previous=>{
      const next=new Set(previous)
      ids.forEach(id=>checked?next.add(id):next.delete(id))
      return next
    })
  }
  return <><div className="ws-heading"><div><h1>分析任务</h1><p>管理轨迹诊断任务和最新结果。</p></div><a className="primary" href="#/tasks/new"><Plus size={16}/>新建分析任务</a></div>
  <div className="ws-toolbar"><div className="ws-segments">{[['all','全部任务'],['attention','需处理'],['completed','已完成'],['draft','草稿']].map(([id,label])=><button key={id} className={status===id?'active':''} aria-pressed={status===id} onClick={()=>updateFilters({search,status:id})}>{label}</button>)}</div><div className="ws-actions"><select aria-label="任务类型" value={kind} onChange={event=>updateFilters({kind:event.target.value})}><option value="all">全部任务类型</option><option value="diagnosis">轨迹诊断</option><option value="failure_patterns">历史失败模式</option></select><input type="search" aria-label="搜索任务" placeholder="搜索任务、Agent 或版本" value={search} onChange={e=>updateFilters({search:e.target.value,status})}/>{search||status!=='all'||kind!=='all'?<button className="text-button" onClick={()=>updateFilters({search:'',status:'all',kind:'all'})}>清除筛选</button>:null}</div></div>
  {error?<div className="ws-actions" role="alert"><p className="error-inline">{error}</p><button className="secondary small" onClick={refresh}>重试加载</button></div>:null}
  <div className="ws-selection">
    <TableSelection pageIds={currentPage.map(task=>task.id)} selectedIds={selectedIds} total={filtered.length} disabled={busy||!tasks}
      onPage={checked=>selectTasks(currentPage.map(task=>task.id),checked)}
      onAll={checked=>setSelected(new Set(checked?filtered.map(task=>task.id):[]))}/>
    <strong aria-live="polite">已选 {selectedIds.size} 个任务</strong>
    <div className="ws-actions"><DeleteRecords records={selectedTasks} kind="task" onDeleted={ids=>selectTasks(ids,false)}/></div>
  </div>
  <section className="ws-surface ws-table-frame" aria-busy={!tasks && !error}>{filtered.length ? <><div ref={tableViewport} className="ws-table-scroll" role="region" aria-label="分析任务表格" tabIndex={0}><table className="ws-task-table"><thead><tr><th>任务 / Agent</th><th>轨迹数</th><th>最新状态</th><th>处理进度或结果</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{currentPage.map(t=>{
    const pattern=t.kind==='failure_patterns'
    const href=pattern?patternTaskPath(t.id):`#/tasks/${t.id}`
    return <tr key={t.id}><td><div className="ws-task-start"><input type="checkbox" aria-label={`选择任务 ${t.name}`} checked={selectedIds.has(t.id)} disabled={busy} onChange={event=>selectTasks([t.id],event.target.checked)}/><div><a className="ws-task-name" href={href}>{t.name}</a><small>{pattern?'历史失败模式':["轨迹诊断",t.agent,t.version].filter(Boolean).join(' · ')}</small></div></div></td><td data-label="轨迹数">{t.input_count} 条</td><td data-label="状态"><Badge value={t.status}/>{t.counts.failed?<small className="ws-error">{t.counts.failed} 条分析失败</small>:null}{t.counts.feedback_failed?<small className="ws-error">{t.counts.feedback_failed} 条建议生成失败</small>:null}</td><td data-label="处理进度或结果">{pattern?<span>{t.counts.repeated} 个重复模式</span>:<><span>{t.counts.completed??0} / {t.input_count}</span><progress aria-label={t.name+'诊断进度'} max={t.input_count||1} value={t.counts.completed??0}/></>}</td><td className="muted" data-label="创建时间">{dateLabel(t.created_at)}</td><td><div className="ws-actions"><a href={href}>查看</a>{!pattern?<a className="ws-copy" aria-label={`复制 ${t.name}`} href={`#/tasks/new?source=${t.id}`}><Copy size={13}/>复制任务</a>:null}<DeleteRecord id={t.id} name={t.name} kind={pattern?'failure_patterns':'diagnosis'} compact onDeleted={()=>selectTasks([t.id],false)}/></div></td></tr>})}</tbody></table></div><Pagination entity="任务" unit="个" page={page} pages={pages} pageSize={pageSize} total={filtered.length} onPage={setPage} onSize={size=>{setPageSize(size);setPage(1)}}/></> : !error ? <div className="empty">
    {tasks?.length ? <><h3>没有符合筛选条件的任务</h3><p>调整搜索关键词或清除筛选条件后重试。</p></> :
      tasks ? <><h3>暂无分析任务</h3><p>新建任务并上传待诊断的轨迹。</p></> : <p role="status">正在加载分析任务…</p>}
  </div> : null}</section>
  </>
}
