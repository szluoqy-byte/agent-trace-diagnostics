import DiagnosisDepthField, {defaultDiagnosisLevel, diagnosisDepthLabel, diagnosisLevelFor} from '../components/DiagnosisDepthField'
import type {DiagnosisLevel,DiagnosisMode} from '../types'
import TaskImprovements from './TaskImprovements'
import {useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react'
import {Copy,Download,Play,RotateCcw} from 'lucide-react'
import {uiText} from '../branding'
import {dateLabel,post} from '../api'
import DeleteRecord from '../components/DeleteRecord'
import {Dialog} from '../components/Dialogs'
import Pagination from '../components/Pagination'
import TableSelection, {useSelectionRequest} from '../components/TableSelection'
import Tabs from '../components/Tabs'
import {Badge,diagnosisStatus,filterQuery,formatLabel,listKey,listScroll,reviewStatus,taskPath,tracePath,useResource,useWorkspace} from '../workspace'
import type {ActionResult,AnalysisRun,AnalysisTask,Counts,RunPage,TaskRow} from '../workspace'

const selections=new Map<string,{filter:string;ids:string[]}>()

export function TaskStats({counts}: {counts:Counts}) {
  const c=(key:string)=>counts[key]??0
  return <div className="ws-stats"><div><span>任务轨迹</span><strong>{c('total')}<small>条</small></strong><p>业务失败 {c('business_failed')} · 业务成功 {c('business_succeeded')}</p></div><div><span>诊断已完成</span><strong>{c('completed')}<small>/ {c('total')}</small></strong><p>待分析 {c('pending')} · 排队 / 运行 {c('queued')+c('running')}</p></div><div><span>分析失败</span><strong className={c('failed')?'ws-error':''}>{c('failed')}<small>条</small></strong><p>可只重试失败项</p></div><div><span>单条建议</span><strong>{c('feedback_completed')}<small>/ {c('feedback_applicable')}</small></strong><p>{c('feedback_failed')?`${c('feedback_failed')} 条生成失败`:'已定位有效关键错误的失败轨迹'}</p></div><div><span>待人工复核</span><strong>{c('review_pending')}<small>条</small></strong><p>已复核 {c('reviewed')} 条 · 免复核 {c('review_waived')} 条</p></div></div>
}

export default function TaskDetail({id,params}:{id:string;params:URLSearchParams}) {
  const {version,busy,config,action,go,openSettings}=useWorkspace(),{data:task,error}=useResource<AnalysisTask>(`/tasks/${id}`,version,2000)
  const [modal,setModal]=useState(false),[autoFeedback,setAutoFeedback]=useState(false),[startError,setStartError]=useState('')
  const [diagnosisMode,setDiagnosisMode]=useState<DiagnosisLevel>(defaultDiagnosisLevel)
  const [showStats,setShowStats]=useState(false)
  const close=useCallback(()=>setModal(false),[])
  const run=task?.runs[0]
  const requestedTab=params.get('tab')||''
  const tab=requestedTab==='config'||(requestedTab==='improvements'&&run)?requestedTab:'data'
  function openStart(){setDiagnosisMode(diagnosisLevelFor((run?.config.diagnosis_mode as DiagnosisMode|undefined)??task?.diagnosis_mode));setAutoFeedback(run?.auto_feedback??task?.draft_auto_feedback??false);setStartError('');setModal(true)}
  const query=new URLSearchParams(params)
  query.delete('run')
  query.delete('improvement')
  query.delete('category')
  if(requestedTab==='history')query.delete('tab')
  const canonicalQuery=query.toString()
  useEffect(()=>{
    if(params.toString()!==canonicalQuery)go(taskPath(id,new URLSearchParams(canonicalQuery)),true)
  },[canonicalQuery,go,id,params])
  function switchTab(next:string){const q=new URLSearchParams(query);q.set('tab',next);go(taskPath(id,q))}
  async function start(){
    const result=await action(()=>post<{run_id:string}>(`/tasks/${id}/runs`,{
      diagnosis_mode:diagnosisMode,auto_feedback:autoFeedback,
    }),run?'轨迹已加入重新分析队列':'轨迹已加入分析队列',setStartError)
    if(result){close();go(taskPath(id,new URLSearchParams()),true)}
  }
  if(error&&!task)return <div className="empty"><h1>无法读取任务</h1><p role="alert">{error}</p><a href="#/tasks">返回任务列表</a></div>
  if(!task)return <div className="empty">正在读取任务…</div>
  const active=task.counts.running>0||task.counts.queued>0||task.counts.feedback_active>0||!!run?.improvement_active
  const taskContext=[task.agent,task.version,task.goal].filter(Boolean).join(' · ')
  return <><div className="ws-breadcrumb"><a href="#/tasks">分析任务</a><span>/</span><span>任务详情</span></div><div className="ws-heading"><div><div className="ws-heading-title"><h1>{task.name}</h1><Badge value={run?.status??task.status}/></div>{taskContext ? <p className="ws-task-context" title={taskContext}>{taskContext}</p> : null}</div><div className="ws-actions"><a className="secondary" href={`#/tasks/new?source=${id}`}><Copy size={15}/>复制任务</a>{run?<a className="secondary" href={`/api/tasks/${id}/runs/${run.id}/export`} download><Download size={15}/>导出当前分析</a>:null}<DeleteRecord id={id} name={task.name} kind="diagnosis"
    blocked={active?'仍有诊断或改进建议在排队或运行，请处理完成后再删除。':undefined} onDeleted={()=>go('/tasks',true)}/></div></div>
  {run?<div className="ws-run-row">
    <span className="muted">{String(run.config.model||'历史配置')} · {diagnosisDepthLabel((run.config.diagnosis_mode as DiagnosisMode|undefined)??'trajdebug')} · {run.config.concurrency?`轨迹并发上限 ${run.config.concurrency}`:'未记录并发上限'}</span>
    <button className="secondary" disabled={busy||active} onClick={openStart} title={active?'请等待当前分析和建议生成结束':undefined}><RotateCcw size={14}/>重新分析</button>
  </div>:null}
  <div className="ws-task-overview" data-expanded={showStats} onKeyDown={event=>{if(event.key==='Escape')setShowStats(false)}}>
    <button type="button" className="text-button" aria-controls="task-stats" aria-expanded={showStats}
      onClick={()=>setShowStats(!showStats)}>{showStats?'收起任务概览':'查看任务概览'}</button>
    <div id="task-stats"><TaskStats counts={run?.counts??task.counts}/></div>
  </div>
  <Tabs id="task" label="任务内容" className="ws-tabs" items={[{id:'data',label:'轨迹列表'},...(run?[{id:'improvements',label:'任务改进建议'}]:[]),{id:'config',label:'数据与配置'}]} active={tab} onChange={switchTab}/>
  <div className={tab==='data'?'ws-fit-route':'ws-content-scroll'} role="tabpanel" key={tab}
    id={`task-panel-${tab}`} aria-labelledby={`task-tab-${tab}`}>
  {tab==='data'?<>
    {!run?<div className="ws-surface ws-draft-banner"><div><h2>任务草稿</h2><p>首次启动前可核对轨迹和诊断模型。</p></div>
      {config?.ready?<button className="primary" disabled={busy} onClick={openStart}><Play size={15}/>启动分析</button>:<button className="secondary" onClick={openSettings}>配置模型</button>}
    </div>:null}
    <TraceTable key={`${id}/${run?.id??'draft'}`} task={task} run={run} params={query}/>
  </>:tab==='improvements'&&run?<TaskImprovements key={run.id} taskId={id} runId={run.id}/>:<TaskConfig task={task} run={run}/>}</div>
  {modal?<Dialog title={run?'重新分析':'启动首次分析'} onClose={close} busy={busy}><div className="modal-body"><p>使用当前任务的 {task.input_count} 条轨迹。{run?'重新分析会更新任务展示的诊断结果和改进建议。请复核新结论。':''}</p><dl className="settings-list"><dt>提示词语言</dt><dd>{task.prompt_language==='zh'?'中文':'English'}</dd><dt>诊断模型</dt><dd>{config?.model||'尚未配置'}</dd><dt>轨迹并发上限</dt><dd>{config?.concurrency}</dd><dt>模型请求并发上限</dt><dd>{config?.llm_concurrency}</dd><dt>请求重试次数</dt><dd>{config?.max_retries}</dd><dt>请求超时</dt><dd>{config?.timeout} 秒</dd><dt>输出上限</dt><dd>{config?.max_tokens} tokens</dd>{diagnosisMode==='deep'?<><dt>聚类 JSON 模式</dt><dd>{config?.cluster_json_mode?'已开启':'已关闭'}</dd></>:null}</dl><DiagnosisDepthField value={diagnosisMode} onChange={setDiagnosisMode} disabled={busy}/><label className="ws-check"><input type="checkbox" checked={autoFeedback} onChange={e=>setAutoFeedback(e.target.checked)}/><span>诊断后自动生成单条建议<small>对已定位关键失败步骤的轨迹逐条生成建议，会额外调用模型。</small></span></label><p className="ws-help">使用当前系统配置保存本次分析配置。如需重试失败项，请使用轨迹列表中的「重试失败诊断」。</p>{startError?<p role="alert" className="error-inline">{startError}</p>:null}</div><footer><button className="secondary" disabled={busy} onClick={close}>取消</button><button className="primary" disabled={busy||!config?.ready||active} onClick={()=>void start()}>{busy?'正在提交…':'开始分析'}</button></footer></Dialog>:null}</>
}

function TraceTable({task,run,params}:{task:AnalysisTask;run:AnalysisRun|undefined;params:URLSearchParams}) {
  const {version,busy,action,go,notify,openSettings,config,refresh}=useWorkspace()
  const runId=run?.id??'draft',scope=`${task.id}/${runId}`,filters=filterQuery(params)
  const [batch,setBatch]=useState<{kind:string;label:string;count:number;ids?:string[]}|null>(null)
  const [batchError,setBatchError]=useState('')
  const [showTools,setShowTools]=useState(false)
  const closeBatch=useCallback(()=>setBatch(null),[])
  const requestedPage=Number(params.get('page'))
  const page=Number.isSafeInteger(requestedPage)&&requestedPage>0?requestedPage:1,size=[10,20,50].includes(Number(params.get('page_size')))?Number(params.get('page_size')):10
  const traceListPath=`/tasks/${task.id}/runs/${runId}/traces`
  const path=`${traceListPath}?page=${page}&page_size=${size}${filters?'&'+filters:''}`
  const {data,error}=useResource<RunPage>(path,version,run?1800:0)
  const allSelection=useSelectionRequest(`${traceListPath}?selection=true&${filters}`)
  const tableViewport=useRef<HTMLDivElement>(null)
  const [selected,setSelected]=useState<string[]>(()=>{const saved=selections.get(scope);return saved?.filter===filters?saved.ids:[]})
  const urlSearch=params.get('search')||'',[searchValue,setSearchValue]=useState(urlSearch)
  const composing=useRef(false)
  useEffect(()=>setSearchValue(urlSearch),[urlSearch])
  const previousFilter=useRef(filters),restored=useRef('')
  const readonly=!run||data?.read_only||run.read_only,counts=data?.counts??run?.counts??task.counts
  const pageIds=data?.items.map(r=>r.id)??[],selectedIds=new Set(selected)
  const selectionBusy=busy||allSelection.loading
  function select(ids:string[]){selections.set(scope,{filter:filters,ids});setSelected(ids)}
  function selectPage(checked:boolean){
    const currentPage=new Set(pageIds)
    select(checked?[...new Set([...selected,...pageIds])]:selected.filter(id=>!currentPage.has(id)))
  }
  useEffect(()=>{if(previousFilter.current!==filters){previousFilter.current=filters;selections.set(scope,{filter:filters,ids:[]});setSelected([])}},[scope,filters])
  useEffect(()=>{if(data&&data.page!==page){const q=new URLSearchParams(params);q.set('page',String(data.page));go(taskPath(task.id,q),true)}},[data?.page,page,task.id])
  const restoreKey=listKey(task.id,params)
  const focusId=params.get('focus')
  useLayoutEffect(()=>{
    const viewport=tableViewport.current
    if(!viewport||!data||data.page!==page||data.page_size!==size||restored.current===restoreKey)return
    restored.current=restoreKey
    viewport.scrollTop=listScroll.get(restoreKey)??0
    const row=focusId?document.getElementById(`row-${focusId}`):null
    if(!row||!viewport.contains(row))return
    const bounds=viewport.getBoundingClientRect()
    const rowBounds=row.getBoundingClientRect()
    const rowsTop=bounds.top+(viewport.querySelector('thead')?.offsetHeight??0)
    if(rowBounds.top<rowsTop)viewport.scrollTop+=rowBounds.top-rowsTop
    else if(rowBounds.bottom>bounds.bottom)viewport.scrollTop+=rowBounds.bottom-bounds.bottom
  },[data,focusId,restoreKey,page,size])
  function query(patch:Record<string,string>,reset=false){const q=new URLSearchParams(params);q.delete('focus');Object.entries(patch).forEach(([k,v])=>v==='all'||v===''?q.delete(k):q.set(k,v));if(reset){q.set('page','1');select([])}go(taskPath(task.id,q),reset)}
  function ask(kind:string,label:string,count:number,ids?:string[]){
    setBatchError('')
    setShowTools(false)
    setBatch({kind,label,count,ids})
  }
  async function perform(){
    if(!batch||!run)return
    const {kind,ids}=batch
    const result=await action(()=>post<ActionResult>(`/tasks/${task.id}/runs/${run.id}/actions`,{action:kind,...(ids?{trace_ids:ids}:{})}),undefined,setBatchError)
    if(!result)return
    closeBatch()
    if(result.enqueued&&ids)select([])
    notify(result.enqueued
      ? `已提交 ${result.enqueued} 条${kind==='feedback'?'建议生成':'诊断'}请求${result.skipped.length?`，跳过 ${result.skipped.length} 条不适用的轨迹`:''}`
      : result.skipped[0]?.reason||'所选范围内没有需要处理的轨迹')
  }
  function open(row:TaskRow){listScroll.set(restoreKey,tableViewport.current?.scrollTop??0);go(tracePath(task.id,row.id,params))}
  const exportSelected='/api/tasks/'+task.id+'/runs/'+runId+'/export?'+new URLSearchParams(selected.map(id=>['trace_id',id]))
  const feedbackTodo=Math.max(0,(counts.feedback_applicable??0)-(counts.feedback_completed??0)-(counts.feedback_active??0))
  return <><div className="ws-task-table-tools" data-expanded={showTools} onKeyDown={event=>{if(event.key==='Escape')setShowTools(false)}}>
  <button type="button" className="secondary small ws-table-tools-toggle" aria-expanded={showTools} aria-controls="task-table-controls"
    onClick={()=>setShowTools(!showTools)}>{run?'筛选与批量操作':'筛选轨迹'}{filters?' · 已筛选':''}{selected.length?` · 已选 ${selected.length} 条`:''}</button>
  <div id="task-table-controls">
  {run?<div className="ws-toolbar"><div className="ws-actions"><button className="primary" disabled={busy||readonly||!counts.pending} onClick={()=>ask('pending','诊断待处理轨迹',counts.pending)}>诊断待处理轨迹（{counts.pending??0}）</button><button className="secondary" disabled={busy||readonly||!counts.failed} onClick={()=>ask('retry_failed','重试失败诊断',counts.failed)}>重试失败诊断（{counts.failed??0}）</button><button className="secondary" disabled={busy||readonly||!feedbackTodo} onClick={()=>ask('feedback','逐条生成改进建议',feedbackTodo)}>逐条生成建议（{feedbackTodo}）</button></div><span className="muted ws-batch-scope">{readonly?'历史记录只读':'批量操作覆盖当前任务全部符合条件的轨迹'}</span></div>:null}
  <div className="ws-filters"><input type="search" aria-label="搜索轨迹" placeholder="搜索标题、Agent、ID 或来源" value={searchValue}
    onCompositionStart={()=>{composing.current=true}}
    onCompositionEnd={event=>{composing.current=false;query({search:event.currentTarget.value},true)}}
    onChange={event=>{setSearchValue(event.target.value);if(!composing.current)query({search:event.target.value},true)}}/><select aria-label="诊断状态" value={params.get('diagnosis')||'all'} onChange={e=>query({diagnosis:e.target.value},true)}><option value="all">全部诊断状态</option>{Object.entries(diagnosisStatus).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select><select aria-label="业务结果" value={params.get('business')||'all'} onChange={e=>query({business:e.target.value},true)}><option value="all">全部业务结果</option><option value="failed">业务失败</option><option value="succeeded">业务成功</option><option value="unknown">结果未知</option></select>{run?<select aria-label="复核状态" value={params.get('review')||'all'} onChange={e=>query({review:e.target.value},true)}><option value="all">全部复核状态</option><option value="pending">待复核</option>{Object.entries(reviewStatus).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>:null}{filters?<button className="text-button" onClick={()=>{select([]);go(taskPath(task.id,new URLSearchParams({...(run?{run:runId}:{}),page_size:String(size)})),true)}}>清除筛选</button>:null}</div>
  {run?<div className="ws-selection">
    <TableSelection pageIds={pageIds} selectedIds={selectedIds} total={data?.total??0} loading={allSelection.loading}
      disabled={selectionBusy||!data} onPage={selectPage}
      onAll={checked=>checked?void allSelection.load<string>(select):select([])}/>
    <strong aria-live="polite">已选 {selected.length} 条</strong>
    {selected.length?<div className="ws-actions">
      <button className="secondary small" disabled={selectionBusy||readonly} onClick={()=>ask('diagnose','诊断所选轨迹',selected.length,selected)}>诊断所选</button>
      <button className="secondary small" disabled={selectionBusy||readonly} onClick={()=>ask('feedback','为所选轨迹生成建议',selected.length,selected)}>生成所选单条建议</button>
      <a className="secondary small" href={exportSelected} download>导出所选</a>
    </div>:null}
  </div>:null}
  </div></div>
  {allSelection.error?<p className="error-inline" role="alert">{allSelection.error}</p>:null}
  {error?<p className="error-inline" role="alert">{error}</p>:null}
  <p className="ws-table-scroll-hint muted">横向滑动可查看其余列</p>
  <section className="ws-surface ws-table-frame" aria-busy={!data&&!error}><div ref={tableViewport} className="ws-table-scroll" role="region" aria-label="任务轨迹表格" tabIndex={0}><table className="ws-trace-table"><thead><tr><th aria-label="选择"/><th>轨迹 / 来源</th><th>Agent</th><th>业务结果</th><th>诊断状态</th><th>关键步骤</th><th>改进建议</th><th>人工复核</th><th>操作</th></tr></thead><tbody>{data?.items.map(r=><tr key={r.id} id={`row-${r.id}`} className={params.get('focus')===r.id?'ws-focused-row':''}><td>{run?<input type="checkbox" aria-label={`选择轨迹 ${r.id}`} checked={selectedIds.has(r.id)} disabled={selectionBusy} onChange={e=>select(e.target.checked?[...selected,r.id]:selected.filter(id=>id!==r.id))}/>:null}</td><td><a href={'#'+tracePath(task.id,r.id,params)} className="ws-trace-title" onClick={event=>{if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();open(r)}}>{r.title}</a><small>{formatLabel(r.input_format)} · {r.steps} 条消息</small></td><td className="ws-agent-cell">{r.agent_name||<span className="muted">未识别</span>}</td><td><Badge value={r.outcome}>{r.outcome==='failed'?'业务失败':r.outcome==='succeeded'?'业务成功':'结果未知'}</Badge></td><td><Badge value={r.diagnosis}>{diagnosisStatus[r.diagnosis]}</Badge>{r.diagnosis==='running'||r.diagnosis==='queued'?<small>{uiText(r.stage)} · {r.progress??0}%</small>:r.error?<small className="ws-error" title={uiText(r.error)}>请求失败 · {r.attempts} 次尝试</small>:null}</td><td>{r.diagnosis==='completed'&&r.outcome==='failed'?r.critical_step!=null?<strong>#{r.critical_step}</strong>:<span className="muted">{r.step_pending_review?'待复核':'未定位'}</span>:'—'}</td><td>{(r.feedback_applicable||r.feedback_status)?<Badge value={r.feedback_status??'pending'}>{{completed:'已生成',failed:'生成失败',queued:'排队中',running:'生成中'}[r.feedback_status??'']??'未生成'}</Badge>:<span className="muted">{r.step_pending_review?'复核后可生成':r.diagnosis==='completed'?'不适用':'待诊断'}</span>}</td><td>{r.diagnosis==='completed'?<Badge value={r.review_status??'pending'}>{reviewStatus[r.review_status??'']??'待复核'}</Badge>:'—'}</td><td><button className="text-button" onClick={()=>open(r)}>查看详情</button></td></tr>)}</tbody></table></div>{!data||!data.items.length?<div className="empty">{data?'没有符合条件的轨迹，可清除筛选后再试。':error?<button className="secondary" onClick={refresh}>重新加载轨迹</button>:'正在读取轨迹列表…'}</div>:null}<Pagination page={data?.page??1} pages={data?.pages??1} pageSize={size} total={data?.total??0} onPage={n=>{if(!data||n===data.page)return;query({page:String(n)})}} onSize={n=>query({page_size:String(n),page:'1'})} loading={!data}/></section>
  {batch?<Dialog title={batch.label} onClose={closeBatch} busy={busy}>
    <div className="modal-body">
      <p>{batch.ids?`处理已勾选的 ${batch.count} 条轨迹，包含跨页选择。`:`预计处理 ${batch.count} 条符合条件的轨迹，包含筛选范围以外的轨迹。`}</p>
      <p className="muted">{batch.kind==='feedback'?'根据当前诊断结果生成改进建议，跳过未完成诊断、已有建议或不适用的轨迹。':'仅提交符合条件的轨迹，已完成和正在分析的轨迹会跳过。'}</p>
      {batchError?<p className="error-inline" role="alert">{batchError}</p>:null}
      {!config?.ready?<button className="text-button" onClick={()=>{closeBatch();openSettings()}}>查看模型设置</button>:null}
    </div>
    <footer><button className="secondary" disabled={busy} onClick={closeBatch}>取消</button><button className="primary" disabled={busy} onClick={()=>void perform()}>{busy?'正在提交…':'确认提交'}</button></footer>
  </Dialog>:null}</>
}

function TaskConfig({task,run}:{task:AnalysisTask;run:AnalysisRun|undefined}) {
  const c=run?.config
  return <section className="ws-surface ws-config"><h3>数据范围</h3><dl><dt>轨迹数量</dt><dd>{task.input_count} 条</dd><dt>数据格式</dt><dd>{task.formats.map(formatLabel).join('、')}</dd><dt>业务结果</dt><dd>失败 {task.counts.business_failed??0} / 成功 {task.counts.business_succeeded??0}</dd><dt>创建时间</dt><dd>{dateLabel(task.created_at)}</dd>{task.goal?<><dt>排查目标</dt><dd>{task.goal}</dd></>:null}</dl><h3>分析配置</h3>{c?<><dl><dt>分析开始时间</dt><dd>{dateLabel(run!.created_at)}</dd><dt>诊断等级</dt><dd>{diagnosisDepthLabel((c.diagnosis_mode as DiagnosisMode|undefined)??'trajdebug')}</dd><dt>提示词语言</dt><dd>{c.prompt_language==='zh'?'中文':'English'}</dd><dt>诊断模型</dt><dd>{String(c.model||'未保留')}</dd><dt>接口地址</dt><dd>{String(c.base_url||'未保留')}</dd><dt>轨迹并发上限</dt><dd>{String(c.concurrency??'未保留')}</dd><dt>启动时模型请求并发上限</dt><dd>{String(c.llm_concurrency??'未保留')}</dd><dt>请求重试次数</dt><dd>{String(c.max_retries??'未保留')}</dd><dt>请求超时</dt><dd>{c.timeout==null?'未保留':`${String(c.timeout)} 秒`}</dd><dt>输出上限</dt><dd>{c.max_tokens==null?'未保留':`${String(c.max_tokens)} tokens`}</dd><dt>聚类 JSON 模式</dt><dd>{c.cluster_json_mode==null?'未保留':c.cluster_json_mode?'已开启':'已关闭'}</dd><dt>改进建议</dt><dd>{run!.auto_feedback?'诊断完成后自动生成':'按需生成'}</dd></dl>{c.note?<p className="ws-info">{String(c.note)}</p>:null}</>:<p className="muted">尚未启动分析。启动时保存当前执行配置。</p>}</section>
}
