import {useCallback, useLayoutEffect, useRef, useState} from 'react'
import {Download} from 'lucide-react'
import {dateLabel, post} from '../api'
import DeleteRecord from '../components/DeleteRecord'
import {Dialog} from '../components/Dialogs'
import Pagination from '../components/Pagination'
import {Badge, useResource, useWorkspace} from '../workspace'
import {patternStates, patternTaskPath, responsibilityLabels} from '../patterns'
import type {FailurePattern, PatternMembers, PatternReview, PatternRun, PatternTask} from '../patterns'

const percent = new Intl.NumberFormat('zh-CN',{style:'percent',maximumFractionDigits:1})
const reviewLabels = {confirmed: '已确认', suspect: '分组存疑'}
const evidenceLabels = {commitment: '错误证据', reference: '参考依据', lifecycle: '生命周期证据'}

export default function PatternDetail({id, requestedRun, requestedPattern}: {id: string; requestedRun: string | null; requestedPattern: string | null}) {
  const {data: task, error} = useResource<PatternTask>('/pattern-tasks/'+id)
  const runId = requestedRun ?? task?.runs[0]?.id
  return <>
    <div className="ws-breadcrumb"><a href="#/tasks">分析任务</a><span>/</span><span>失败模式分析</span></div>
    {error ? <p className="error-inline" role="alert">{error}</p> : task && runId ?
      <PatternResult key={runId + (requestedPattern ?? '')} task={task} runId={runId} requestedPattern={requestedPattern}/> : <p role="status">正在读取分析任务…</p>}
  </>
}

function PatternResult({task, runId, requestedPattern}: {task: PatternTask; runId: string; requestedPattern: string | null}) {
  const {version, refresh, go} = useWorkspace()
  const path = `/pattern-tasks/${task.id}/runs/${runId}`
  const {data: run, error} = useResource<PatternRun>(path, version)
  const [activeView, setView] = useState<string|null>(null)
  const [selected, setSelected] = useState<string|null>(requestedPattern)
  const [reviewPattern, setReviewPattern] = useState<FailurePattern|null>(null)
  const closeReview = useCallback(() => setReviewPattern(null), [])
  const readonly = task.runs[0].id !== runId
  const report = run?.result
  const linkedPattern = report?.patterns.find(pattern => pattern.id === requestedPattern)
  const view = activeView ?? (linkedPattern?.count === 1 ? 'single' : 'repeated')
  const visible = report?.patterns.filter(pattern => (pattern.count >= 2) === (view === 'repeated')) ?? []

  return <>
    <div className="ws-heading">
      <div><h1>{task.name}</h1><p>历史失败模式分析</p></div>
      <div className="ws-actions">
      <DeleteRecord id={task.id} name={task.name} kind="failure_patterns" onDeleted={()=>go('/tasks',true)}/>
      </div>
    </div>
    <div className="ws-run-row">
      <div><label>分析记录 <select aria-label="选择失败模式分析记录" value={runId} onChange={event => {
        location.hash = patternTaskPath(task.id,event.target.value).slice(1)
      }}>{task.runs.map(row => <option key={row.id} value={row.id}>第 {row.number} 次 · {dateLabel(row.created_at)}</option>)}</select></label></div>
      {run ? <div className="ws-actions">
        <a className="secondary small" href={'/api'+path+'/export?format=md'} download><Download size={14}/>Markdown</a>
        <a className="secondary small" href={'/api'+path+'/export?format=json'} download>分析数据 JSON</a>
        {(report?.repeated ?? 0) > 0 ? <a className="secondary small" href={'/api'+path+'/export?format=samples'} download>代表轨迹 JSON</a> : null}
      </div> : null}
    </div>
    {error ? <div className="ws-actions" role="alert"><p className="error-inline">{error}</p><button className="secondary" onClick={refresh}>重试</button></div> : null}
    {!report && !error ? <p role="status">正在加载失败模式分析…</p> : null}
    {report && run ? <>
      {readonly ? <p className="ws-warning">正在查看第 {run.number} 次分析的历史记录。</p> : null}
      <div className="ws-stats pattern-stats">
        <div><span>分析范围</span><strong>{report.selected} 条</strong></div>
        <div><span>可分组轨迹 / 业务失败轨迹</span><strong>{report.eligible} / {report.business_failed}</strong></div>
        <div><span>重复模式</span><strong>{report.repeated} 个</strong></div>
        <div><span>单例</span><strong>{report.patterns.length - report.repeated} 条</strong></div>
      </div>
      <div className="ws-toolbar"><div className="ws-segments" aria-label="失败模式分析结果">
        {[['repeated', '重复模式'], ['single', '单例'], ['excluded', `未纳入（${report.selected-report.eligible}）`]].map(([key,label]) =>
          <button key={key} className={view===key?'active':''} aria-pressed={view===key} onClick={() => {
            setView(key)
            setSelected(null)
          }}>{label}</button>)}
      </div><span className="muted">按 {report.eligible} 条可分组轨迹计算占比</span></div>
      {view === 'excluded' ? <PatternMemberTable path={path} taskId={task.id} runId={runId} excluded/> :
        <section key={view} className="pattern-groups ws-content-scroll" aria-label={view==='repeated'?'重复失败模式':'单例'}>
          {!visible.length ? <div className="empty ws-surface">{view==='repeated'?'本轮分析未发现重复模式。':'本轮分析没有仅出现一次的失败模式。'}</div> : null}
          {visible.map(pattern => {
            const review = run.reviews[pattern.id]
            const expanded = selected === pattern.id
            return <article className="ws-surface pattern-group" key={pattern.id}>
              <div className="pattern-group-heading">
                <div><h2>{pattern.object}</h2><p>{[pattern.category,responsibilityLabels[pattern.responsibility] ?? pattern.responsibility].filter(Boolean).join(' · ')}</p></div>
                <strong>{pattern.count}/{pattern.denominator} 条 · {percent.format(pattern.share)}</strong>
              </div>
              <p className="muted">代表轨迹 {pattern.examples.map(sample => sample.title).join('、')}</p>
              {review ? <p><Badge value={review.decision==='confirmed'?'completed':'pending'}>{reviewLabels[review.decision]}</Badge>
                {review.notes ? <span className="pattern-review-note">{review.notes}</span> : null}</p> : null}
              <div className="ws-actions">
                <button className="secondary small" aria-expanded={expanded} onClick={() => setSelected(expanded?null:pattern.id)}>{expanded?'收起轨迹':'查看轨迹与证据'}</button>
                {!readonly ? <button className="text-button" onClick={() => setReviewPattern(pattern)}>复核模式</button> : null}
              </div>
              {expanded ? <PatternMemberTable key={pattern.id} path={path} taskId={task.id} runId={runId} patternId={pattern.id}/> : null}
            </article>
          })}
        </section>}
      <details className="ws-trace-context"><summary>分组说明</summary><p>按责任归属、根因类别和规范化后的错误对象分组。同组包含至少两条轨迹时，计为一个重复模式。</p><p className="muted">分组版本 {run.algorithm_version} · 映射版本 {run.mapping_version}</p></details>
      {reviewPattern ? <PatternReviewDialog path={path} pattern={reviewPattern} existing={run.reviews[reviewPattern.id]} onClose={closeReview}/> : null}
    </> : null}
  </>
}

function PatternMemberTable({path, taskId, runId, patternId, excluded}: {
  path: string; taskId: string; runId: string; patternId?: string; excluded?: boolean
}) {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(10)
  const tableViewport=useRef<HTMLDivElement>(null)
  useLayoutEffect(()=>{if(tableViewport.current)tableViewport.current.scrollTop=0},[page,size,excluded])
  const {version, refresh} = useWorkspace()
  const query = new URLSearchParams({page:String(page),page_size:String(size)})
  if (patternId) query.set('pattern_id',patternId)
  if (excluded) query.set('status','excluded')
  const {data, error, loading} = useResource<PatternMembers>(path+'/members?'+query,version)
  return <section className="ws-surface ws-table-frame pattern-members" aria-label="模式关联轨迹" aria-busy={loading}>
    <div ref={tableViewport} className="ws-table-scroll" role="region" aria-label="模式关联轨迹表格" tabIndex={0}><table><thead><tr><th>轨迹</th><th>诊断与证据</th><th>来源</th></tr></thead>
      <tbody>{data?.items.map(item => {
        const source = new URLSearchParams({run:item.source_run_id??'',pattern_task:taskId,pattern_run:runId})
        if (patternId) source.set('pattern_id',patternId)
        const href = item.source_task_id && item.source_run_id
          ? `#/tasks/${item.source_task_id}/traces/${item.trace_id}?${source}` : `#/library/${item.trace_id}`
        return <tr key={item.trace_id}><td><strong>{item.title}</strong>{item.tool?<small>{item.tool}</small>:null}{item.lifecycle?<small>生命周期 {item.lifecycle}</small>:null}</td>
          <td>{item.evidence ? <><small>
            {item.evidence_role?`${evidenceLabels[item.evidence_role]} · `:''}{item.evidence.source_kind==='compressed'?'压缩视图':'原文'} · 步骤 #{item.evidence.step}</small>
            <span className="pattern-quote">{item.evidence.quote}</span></> : <>{patternStates[item.state]}
              {item.critical_step!=null?<small>关键步骤 #{item.critical_step}{item.category?` · ${item.category}`:''}</small>:null}</>}
              {item.instance_source==='unique_same_step'?<small>按同一步骤与类别关联</small>:null}</td>
          <td><a href={href}>{item.evidence?'核对证据':item.source_task_id?'查看来源诊断':'查看轨迹'}</a>
            {item.source_task ? <small>{item.source_task} · 第 {item.source_run_number} 次</small> : null}</td></tr>
      })}</tbody>
    </table></div>
    {error ? <div role="alert" className="ws-actions"><p className="error-inline">{error}</p><button className="secondary" onClick={refresh}>重试</button></div> : null}
    {data && !data.items.length ? <p className="empty">本轮没有符合条件的轨迹。</p> : null}
    <Pagination page={data?.page??page} pages={data?.pages??1} total={data?.total??0} pageSize={size} loading={!data}
      onPage={setPage} onSize={next => {setSize(next);setPage(1)}}/>
  </section>
}

function PatternReviewDialog({path, pattern, existing, onClose}: {
  path: string; pattern: FailurePattern; existing?: PatternReview; onClose: () => void
}) {
  const {action, busy} = useWorkspace()
  const [decision, setDecision] = useState(existing?.decision ?? 'confirmed')
  const [notes, setNotes] = useState(existing?.notes ?? '')
  const [error, setError] = useState('')
  async function save() {
    const result = await action(() => post(`${path}/patterns/${pattern.id}/review`, {decision, notes}), '已保存模式复核', setError)
    if (result) onClose()
  }
  return <Dialog title="复核失败模式" onClose={onClose} busy={busy}>
    <div className="modal-body"><p>{pattern.object}</p><label>复核结论<select value={decision}
      onChange={event => setDecision(event.target.value as PatternReview['decision'])}>
      <option value="confirmed">确认模式</option><option value="suspect">标记分组存疑</option>
    </select></label><label>备注<textarea rows={3} maxLength={2000} value={notes} onChange={event => setNotes(event.target.value)}/></label>
      {error?<p role="alert" className="error-inline">{error}</p>:null}</div>
    <footer><button className="secondary" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy} onClick={() => void save()}>保存复核</button></footer>
  </Dialog>
}
