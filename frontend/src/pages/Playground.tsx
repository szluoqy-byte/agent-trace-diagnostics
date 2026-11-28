import {useEffect, useRef, useState} from 'react'
import {ArrowUpRight, Play} from 'lucide-react'
import {api} from '../api'
import {useResource, useWorkspace} from '../workspace'

interface PlaygroundModel {id:string; name:string; model:string; is_default:boolean}
interface TraceInput {
  title:string
  calls:{id:string; label:string}[]
  selected_span:string | null
  parameters:{messages:unknown[]} & Record<string,unknown>
}
interface ModelResult {
  model:string
  duration_seconds:number
  finish_reason?:string
  usage:{prompt_tokens?:number; completion_tokens?:number; total_tokens?:number}
  message:{content?:unknown; refusal?:unknown; tool_calls?:unknown; function_call?:unknown}
}

const emptyRequest = JSON.stringify({messages:[{role:'user', content:''}]}, null, 2)
const integers = new Intl.NumberFormat('zh-CN')
const seconds = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2})
const display = (value:unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const responseLabels: Record<string,string> = {content:'返回内容', refusal:'拒绝原因', tool_calls:'工具调用', function_call:'函数调用'}

export default function Playground({traceId, initialSpan}: {traceId:string|null; initialSpan:string|null}) {
  const {version, openSettings, refresh} = useWorkspace()
  const {data:models, loading:loadingModels, error:modelError} = useResource<{items:PlaygroundModel[]}>(
    '/model-configurations?purpose=playground', version,
  )
  const [spanId, setSpanId] = useState(initialSpan ?? '')
  const seedUrl = traceId ? '/playground/traces/' + encodeURIComponent(traceId)
    + (spanId ? '?' + new URLSearchParams({span_id:spanId}) : '') : null
  const {data:source, loading:loadingSource, error:sourceError} = useResource<TraceInput>(seedUrl)
  const [modelId, setModelId] = useState('')
  const [edit, setEdit] = useState<{spanId:string; text:string} | null>(null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ModelResult | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])

  const selectedModel = models?.items.find(item => modelId ? item.id === modelId : item.is_default)
  const capturedInput = source?.parameters.messages.length ? display(source.parameters) : emptyRequest
  const input = edit?.spanId === spanId ? edit.text : capturedInput
  const selectedSpan = source?.selected_span ?? spanId
  const active = !running && !loadingSource && !!selectedModel && !modelError
  const responseParts = Object.entries(result?.message ?? {}).filter(([,value]) => value != null && value !== '')

  function switchSpan(id:string) {
    setSpanId(id)
    setEdit(null)
    setResult(null)
    setError('')
  }

  async function run() {
    if (!active || !selectedModel || request.current) return
    setError('')
    let parameters: unknown
    try {
      parameters = JSON.parse(input)
      if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)
        || !('messages' in parameters) || !Array.isArray(parameters.messages) || !parameters.messages.length) throw new Error()
    } catch {
      setError('请输入有效的 JSON 请求，其中 messages 必须是非空数组。')
      return
    }
    const controller = new AbortController()
    request.current = controller
    setRunning(true)
    setResult(null)
    try {
      const answer = await api<ModelResult>('/playground/run', {
        method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
        body:JSON.stringify({model_connection_id:selectedModel.id, parameters}),
      })
      if (!controller.signal.aborted) setResult(answer)
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message)
    } finally {
      request.current = null
      if (!controller.signal.aborted) setRunning(false)
    }
  }

  return <div className="playground">
    <div className="ws-heading playground-heading">
      <div><h1>Playground</h1><p>带入轨迹中的模型请求，调整入参后查看模型响应。</p></div>
      <a className="secondary" href="#/library">从轨迹库选择 <ArrowUpRight size={15}/></a>
    </div>
    <div className="playground-content">
      <section className="ws-surface playground-controls" aria-label="调试配置">
        {traceId ? <div className="playground-source">
          <span>来源轨迹</span>
          <a href={`#/library/${encodeURIComponent(traceId)}`}>{source?.title ?? (loadingSource ? '正在读取…' : '查看原轨迹')}</a>
          {source?.calls.length ? <label>调用<select value={selectedSpan} disabled={running}
            onChange={event => switchSpan(event.target.value)}>
            {source.calls.map(call => <option key={call.id} value={call.id}>{call.label}</option>)}
          </select></label> : null}
        </div> : null}
        <div className="playground-run-controls">
          <label>Playground 模型
            <select value={selectedModel?.id ?? ''} disabled={running || loadingModels || !models?.items.length}
              onChange={event => setModelId(event.target.value)}>
              <option value="" disabled>{loadingModels ? '正在加载…' : '请选择模型'}</option>
              {models?.items.map(item => <option key={item.id} value={item.id}>
                {item.name === item.model ? item.model : `${item.name} · ${item.model}`}{item.is_default ? '（默认）' : ''}
              </option>)}
            </select>
          </label>
          <button type="button" className="primary" disabled={!active} onClick={() => void run()}>
            <Play size={15} aria-hidden="true"/>{running ? '调试中…' : '运行调试'}
          </button>
        </div>
        {modelError ? <p className="error-inline" role="alert">{modelError} <button className="text-button" onClick={refresh}>重试加载</button></p>
          : models && !models.items.length ? <p className="ws-help">尚未配置 Playground 模型。 <button className="text-button" onClick={openSettings}>前往系统设置</button></p> : null}
      </section>
      <div className="playground-panels">
        <section className="ws-surface playground-panel">
          <div className="playground-panel-heading"><h2><label htmlFor="playground-request">请求参数</label></h2>
            <button type="button" className="text-button" disabled={running || loadingSource || !edit}
              onClick={() => {setEdit(null); setError('')}}>{traceId ? '恢复带入内容' : '恢复初始请求'}</button>
          </div>
          {sourceError ? <p className="playground-panel-note error-inline" role="alert">{sourceError}
            {spanId ? <button className="text-button" onClick={() => switchSpan('')}>改用默认调用</button> : null}</p>
            : traceId && source && !source.selected_span ? <p className="playground-panel-note muted">
              {source.parameters.messages.length ? '轨迹未记录完整模型请求，已带入可用的输入文本。' : '这条轨迹没有可直接带入的模型输入，可手动填写。'}
            </p> : null}
          <textarea id="playground-request" value={input} spellCheck={false} readOnly={running || loadingSource}
            onChange={event => setEdit({spanId, text:event.currentTarget.value})}/>
          <p className="playground-panel-note muted">运行时将当前请求发送至所选模型服务。</p>
        </section>
        <section className="ws-surface playground-panel" aria-busy={running}>
          <div className="playground-panel-heading"><h2>模型响应</h2>{result ? <span>{seconds.format(result.duration_seconds)} 秒</span> : null}</div>
          <div className="playground-response" role="region" aria-label="模型响应内容" tabIndex={0}>
            {error ? <p role="alert" className="error-inline">{error}</p> : running ? <p className="muted" role="status">等待模型响应…</p>
              : result ? <>
                <dl className="playground-metrics">
                  <div><dt>模型</dt><dd>{result.model}</dd></div>
                  <div><dt>结束原因</dt><dd>{result.finish_reason ?? '—'}</dd></div>
                  <div><dt>输入 Tokens</dt><dd>{result.usage.prompt_tokens == null ? '—' : integers.format(result.usage.prompt_tokens)}</dd></div>
                  <div><dt>输出 Tokens</dt><dd>{result.usage.completion_tokens == null ? '—' : integers.format(result.usage.completion_tokens)}</dd></div>
                </dl>
                {!responseParts.length ? <p className="muted">模型返回内容为空。</p> : null}
                {responseParts.map(([field,value]) => <section className="playground-output" key={field}>
                  <h3>{responseLabels[field]}</h3><pre>{display(value)}</pre>
                </section>)}
              </> : <p className="muted">运行调试后显示模型返回内容。</p>}
          </div>
        </section>
      </div>
    </div>
  </div>
}
