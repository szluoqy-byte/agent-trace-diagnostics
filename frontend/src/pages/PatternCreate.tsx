import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, Play} from 'lucide-react'
import {api, dateLabel, post} from '../api'
import {Badge, useResource, useWorkspace} from '../workspace'
import type {AnalysisTask} from '../workspace'
import {patternStates, patternTaskPath} from '../patterns'
import type {PatternPreview, PatternScope} from '../patterns'

export default function PatternCreate({scope, back, taskId, onCreated}: {
  scope: PatternScope | null; back: string; taskId: string | null; onCreated?: () => void
}) {
  const {action, busy, go} = useWorkspace()
  const [name, setName] = useState(() => '失败模式分析 · ' + new Date().toLocaleDateString('sv-SE'))
  const [sourceRun, setSourceRun] = useState(scope?.run_id)
  const [overrides, setOverrides] = useState<Record<string,string>>({})
  const [retry, setRetry] = useState(0)
  const [loaded, setLoaded] = useState<{key: string; value: PatternPreview} | null>(null)
  const [failure, setFailure] = useState<{key: string; message: string} | null>(null)
  const [submitError, setSubmitError] = useState('')
  const request = useRef<{key: string; id: string} | null>(null)
  const {data: sourceTask} = useResource<AnalysisTask>(scope?.task_id ? '/tasks/'+scope.task_id : null)
  const input = scope ? {scope: {...scope, run_id: sourceRun}, overrides} : null
  const payload = input ? JSON.stringify(input) : ''
  const key = payload + ':' + retry
  const data = loaded?.key === key ? loaded.value : null
  const error = failure?.key === key ? failure.message : ''

  useEffect(() => {
    if (!payload) return
    const controller = new AbortController()
    void api<PatternPreview>('/pattern-tasks/preview', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: payload, signal: controller.signal,
    }).then(value => {
      if (!controller.signal.aborted) setLoaded({key, value})
    }).catch(err => {
      if (!controller.signal.aborted) setFailure({key, message: (err as Error).message})
    })
    return () => controller.abort()
  }, [key, payload])

  const eligible = data?.counts.included ?? 0
  const pending = (data?.counts.pending ?? 0) + (data?.counts.diagnosis_failed ?? 0)
  const missing = data ? data.items.length - eligible - pending : 0

  async function submit() {
    if (!data || !input || eligible < 2 || busy || !name.trim()) return
    setSubmitError('')
    const body = {...input, scope: data.scope, snapshot_hash: data.snapshot_hash, ...(taskId ? {} : {name: name.trim()})}
    const requestKey = JSON.stringify(body)
    if (request.current?.key !== requestKey) request.current = {key: requestKey, id: crypto.randomUUID()}
    const result = await action(() => post<{task_id:string;run_id:string}>(
      taskId ? `/pattern-tasks/${taskId}/runs` : '/pattern-tasks', {...body, request_id: request.current!.id}
    ), '失败模式分析已完成', setSubmitError)
    if (result) {
      onCreated?.()
      go(patternTaskPath(result.task_id, result.run_id).slice(1))
    }
  }

  return <>
    <div className="ws-breadcrumb"><a href="#/tasks">分析任务</a><span>/</span><span>确认分析范围</span></div>
    <div className="ws-heading">
      <div><h1>{taskId ? '重新分析失败模式' : '新建失败模式分析'}</h1><p>选择参与分组的轨迹与来源诊断记录。</p></div>
      <a className="secondary" href={back}><ArrowLeft size={15}/>返回</a>
    </div>
    <section className="ws-surface ws-form-section">
      {!taskId ? <label>任务名称<input maxLength={100} value={name} onChange={event => setName(event.target.value)}/></label> : null}
      {taskId && sourceTask && sourceTask.runs.length > 1 ? <label>来源诊断轮次 <select value={sourceRun} onChange={event => {
        setSourceRun(event.target.value)
        setOverrides({})
        setSubmitError('')
      }}>{sourceTask.runs.map(run => <option value={run.id} key={run.id}>第 {run.number} 次 · {dateLabel(run.created_at)}</option>)}</select></label> : null}
      {!scope ? <p className="ws-warning">请先从诊断任务或轨迹库选择轨迹。</p> : null}
      {error ? <div className="ws-actions"><p className="error-inline" role="alert">{error}</p><button className="secondary" onClick={() => setRetry(n => n+1)}>重新预览</button></div> : null}
      {scope && !error && !data ? <p role="status">正在读取来源诊断…</p> : null}
      {data ? <>
        <div className="ws-preview-summary" role="status"><span>已选 <b>{data.items.length}</b> 条</span><span>可用于分组 <b>{eligible}</b> 条</span>
          <span>诊断待完成 {pending} 条</span><span>其他未纳入分组 {missing} 条</span></div>
        {data.engine_versions.length > 1 ? <p className="ws-warning">所选轨迹的诊断引擎或模型版本不同。请在轨迹清单中核对来源。</p> : null}
        <details className="pattern-preview-list" open={eligible < 2 || undefined}>
          <summary>轨迹及诊断来源（{data.items.length} 条）</summary>
          <div className="ws-table-scroll"><table><thead><tr><th>轨迹</th><th>纳入状态</th><th>来源诊断</th></tr></thead>
            <tbody>{data.items.map(item => {
              const choices = data.choices[item.trace_id] ?? []
              return <tr key={item.trace_id}><td>{item.title}</td><td><Badge value={item.state==='included'?'completed':'pending'}>{patternStates[item.state]}</Badge></td>
                <td>{choices.length > 1 ? <select aria-label={`${item.title}的来源诊断`} value={item.job_id} onChange={event => {
                  const next = event.target.value
                  setOverrides(old => ({...old,[item.trace_id]:next}))
                  setSubmitError('')
                }}>{choices.map(choice => <option key={choice.job_id} value={choice.job_id}>{choice.source_task} · 第 {choice.source_run_number} 次 · {choice.model || '历史配置'}</option>)}</select>
                : item.source_task_id ? <a href={`#/tasks/${item.source_task_id}/traces/${item.trace_id}?run=${item.source_run_id}`}>{item.source_task} · 第 {item.source_run_number} 次</a> : '—'}
                <small>{item.model ?? '—'}</small></td></tr>
            })}</tbody>
          </table></div>
        </details>
        {eligible < 2 ? <p className="ws-warning">至少需要两条有可用归因的失败轨迹。</p> : null}
        <div className="ws-actions pattern-submit">
          <button className="primary" disabled={eligible < 2 || busy || !name.trim()} onClick={() => void submit()}><Play size={15}/>{busy?'正在分析…':'开始失败模式分析'}</button>
          {pending ? <a className="secondary" href={scope?.task_id ? `#/tasks/${scope.task_id}?run=${sourceRun}` : '#/library'}>{scope?.task_id?'返回诊断任务':'从轨迹库发起诊断'}</a> : null}
        </div>
        {submitError ? <div className="ws-actions"><p role="alert" className="error-inline">{submitError}</p><button className="secondary" onClick={() => {
          setSubmitError('')
          setRetry(n => n+1)
        }}>刷新诊断来源</button></div> : null}
      </> : null}
    </section>
  </>
}
