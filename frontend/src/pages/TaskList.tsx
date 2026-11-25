import {useState} from 'react'
import {Plus, Copy} from 'lucide-react'
import DeleteRecord from '../components/DeleteRecord'
import {patternTaskPath} from '../patterns'
import {dateLabel} from '../api'
import {Badge,useResource,useWorkspace} from '../workspace'
import type {AnalysisTask} from '../workspace'

let lastFilters={search:'',status:'all',kind:'all'}

export default function TaskList(){
  const {version,refresh}=useWorkspace(),{data:tasks,error}=useResource<AnalysisTask[]>('/tasks?kind=all',version,2500)
  const [search,setSearch]=useState(lastFilters.search),[status,setStatus]=useState(lastFilters.status),[kind,setKind]=useState(lastFilters.kind)
  function updateFilters(next:Partial<typeof lastFilters>){lastFilters={...lastFilters,...next};setSearch(lastFilters.search);setStatus(lastFilters.status);setKind(lastFilters.kind)}
  const filtered=(tasks??[]) .filter(t=>(kind==='all'||(t.kind??'diagnosis')===kind)&&(`${t.name} ${t.agent} ${t.version}`.toLowerCase().includes(search.trim().toLowerCase()))&&(status==='all'||status==='attention'&&(t.status==='partial'||t.status==='pending'||t.counts.failed>0||t.counts.feedback_failed>0||t.counts.review_pending>0)||status===t.status))
  return <><div className="ws-heading"><div><h1>分析任务</h1><p>查看轨迹诊断与失败模式分析任务。</p></div><a className="primary" href="#/tasks/new"><Plus size={16}/>新建分析任务</a></div>
  <div className="ws-toolbar"><div className="ws-segments">{[['all','全部任务'],['attention','需处理'],['completed','已完成'],['draft','草稿']].map(([id,label])=><button key={id} className={status===id?'active':''} aria-pressed={status===id} onClick={()=>updateFilters({search,status:id})}>{label}</button>)}</div><div className="ws-actions"><select aria-label="任务类型" value={kind} onChange={event=>updateFilters({kind:event.target.value})}><option value="all">全部任务类型</option><option value="diagnosis">轨迹诊断</option><option value="failure_patterns">失败模式分析</option></select><input type="search" aria-label="搜索任务" placeholder="搜索任务、Agent 或版本" value={search} onChange={e=>updateFilters({search:e.target.value,status})}/>{search||status!=='all'||kind!=='all'?<button className="text-button" onClick={()=>updateFilters({search:'',status:'all',kind:'all'})}>清除筛选</button>:null}</div></div>
  {error?<div className="ws-actions" role="alert"><p className="error-inline">{error}</p><button className="secondary small" onClick={refresh}>重试加载</button></div>:null}
  <section className="ws-surface" aria-busy={!tasks && !error}>{filtered.length ? <><div className="ws-table-scroll"><table className="ws-task-table"><thead><tr><th>任务 / Agent</th><th>轨迹数</th><th>最新状态</th><th>处理进度或结果</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{filtered.map(t=>{
    const pattern=t.kind==='failure_patterns'
    const href=pattern?patternTaskPath(t.id):`#/tasks/${t.id}`
    return <tr key={t.id}><td><a className="ws-task-name" href={href}>{t.name}</a><small>{pattern?'失败模式分析':["轨迹诊断",t.agent,t.version].filter(Boolean).join(' · ')} · {t.runs.length} 次分析</small></td><td data-label="轨迹数">{t.input_count} 条</td><td data-label="状态"><Badge value={t.status}/>{t.counts.failed?<small className="ws-error">{t.counts.failed} 条分析失败</small>:null}{t.counts.feedback_failed?<small className="ws-error">{t.counts.feedback_failed} 条建议生成失败</small>:null}</td><td data-label="处理进度或结果">{pattern?<span>{t.counts.repeated} 个重复模式</span>:<><span>{t.counts.completed??0} / {t.input_count}</span><progress aria-label={t.name+'诊断进度'} max={t.input_count||1} value={t.counts.completed??0}/></>}</td><td className="muted" data-label="创建时间">{dateLabel(t.created_at)}</td><td><div className="ws-actions"><a href={href}>查看</a>{!pattern?<a className="ws-copy" aria-label={`复制 ${t.name}`} href={`#/tasks/new?source=${t.id}`}><Copy size={13}/>复制任务</a>:null}<DeleteRecord id={t.id} name={t.name} kind={pattern?'failure_patterns':'diagnosis'} compact/></div></td></tr>})}</tbody></table></div><div className="ws-table-note">共 {filtered.length} 个任务</div></> : !error ? <div className="empty">
    {tasks?.length ? <><h3>没有符合筛选条件的任务</h3><p>调整搜索关键词或清除筛选条件后重试。</p></> :
      tasks ? <><h3>暂无分析任务</h3><p>新建任务并上传待诊断的轨迹。</p></> : <p role="status">正在加载分析任务…</p>}
  </div> : null}</section>
  </>
}
