import {useDeferredValue, useEffect, useMemo, useRef, useState} from 'react'
import {ArrowUpRight, Download, FileCode, Play} from 'lucide-react'
import {api} from '../api'
import {Dialog} from '../components/Dialogs'
import PlaygroundDiff from '../components/PlaygroundDiff'
import Tabs from '../components/Tabs'
import {display, responseLabels} from '../playground'
import {useResource, useWorkspace} from '../workspace'

interface PlaygroundModel {id:string; name:string; model:string; is_default:boolean}
interface TraceInput {
  title:string
  calls:{id:string; label:string}[]
  selected_span:string | null
  parameters:{messages:unknown[]} & Record<string,unknown>
  original_output:({format:'deepagent'|'otel_genai'; message:Record<string,unknown>} & PacketBody) | null
}
interface ModelResult {
  model:string
  duration_seconds:number
  finish_reason?:string
  usage:{prompt_tokens?:number; completion_tokens?:number; total_tokens?:number}
  message:Record<string,unknown>
  raw_response:string
}

interface PacketBody {body:string; is_json:boolean}
type Packet = PacketBody & {title:string; description:string; filename:string}
const resultTabs = [
  {id:'result', label:'调试结果'}, {id:'request', label:'请求对比'}, {id:'output', label:'输出对比'},
]
const emptyRequest = JSON.stringify({messages:[{role:'user', content:''}]}, null, 2)
const integers = new Intl.NumberFormat('zh-CN')
const seconds = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2})

function RequestEditor({value, readOnly, onChange}: {
  value:string; readOnly:boolean; onChange:(value:string)=>void
}) {
  const gutter = useRef<HTMLPreElement>(null)
  const numbers = useMemo(() => Array.from({length:value.split(/\r\n?|\n/).length}, (_, index) => index + 1).join('\n'), [value])

  return <div className="playground-editor">
    <div className="playground-editor-gutter" aria-hidden="true"><pre ref={gutter}>{numbers}</pre></div>
    <textarea id="playground-request" value={value} spellCheck={false} readOnly={readOnly} wrap="off"
      onChange={event => onChange(event.currentTarget.value)}
      onScroll={event => {
        if (gutter.current) gutter.current.style.transform = 'translateY(' + (-event.currentTarget.scrollTop) + 'px)'
      }}/>
  </div>
}

function ModelOutput({message}: {message:Record<string,unknown>}) {
  const fields = Object.entries(responseLabels).filter(([field]) => message[field] != null && message[field] !== '')
  if (!fields.length) return <p className="muted">模型返回内容为空。</p>

  return fields.map(([field, label]) => {
    const value = message[field]
    return <div className="playground-output" key={field}>
      {field !== 'content' || fields.length > 1 ? <span className="playground-output-label">{label}</span> : null}
      {typeof value === 'string' ? <p>{value}</p> : <pre>{display(value)}</pre>}
    </div>
  })
}

function PacketPreview({packet, onClose}: {packet:Packet; onClose:()=>void}) {
  function download() {
    const url = URL.createObjectURL(new Blob([packet.body], {type:packet.is_json ? 'application/json' : 'text/plain;charset=utf-8'}))
    const link = document.createElement('a')
    link.href = url
    link.download = packet.filename + (packet.is_json ? '.json' : '.txt')
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <Dialog title={packet.title} className="playground-packet" onClose={onClose}>
    <p className="playground-panel-note muted">{packet.description}</p>
    <pre tabIndex={0} aria-label="报文内容">{packet.body}</pre>
    <footer><button type="button" className="primary" onClick={download}><Download size={15} aria-hidden="true"/>下载报文</button></footer>
  </Dialog>
}

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
  const [result, setResult] = useState<(ModelResult & {input:string}) | null>(null)
  const [view, setView] = useState('result')
  const [packet, setPacket] = useState<Packet | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])

  const selectedModel = models?.items.find(item => modelId ? item.id === modelId : item.is_default)
  const capturedInput = source?.parameters.messages.length ? display(source.parameters) : emptyRequest
  const input = edit?.spanId === spanId ? edit.text : capturedInput
  const draft = useDeferredValue(input)
  const selectedSpan = source?.selected_span ?? spanId
  const originalOutput = source?.original_output
  const active = !running && !loadingSource && !!selectedModel && !modelError

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
      if (!controller.signal.aborted) setResult({...answer, input})
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message)
    } finally {
      request.current = null
      if (!controller.signal.aborted) setRunning(false)
    }
  }

  function showOriginalPacket() {
    if (!originalOutput) return
    setPacket({
      ...originalOutput, title:'原始输出报文', filename:'playground-original-output',
      description:originalOutput.format === 'otel_genai'
        ? '此调用采集的完整 gen_ai.output.messages 属性。'
        : '此调用采集的完整 span_output。',
    })
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
          <RequestEditor value={input} readOnly={running || loadingSource} onChange={text => setEdit({spanId, text})}/>
          <p className="playground-panel-note muted">运行时将当前请求发送至所选模型服务。</p>
        </section>
        <section className="ws-surface playground-panel" aria-busy={running}>
          <div className="playground-panel-heading">
            <h2>模型响应</h2>
            <div className="playground-packet-actions">
              {originalOutput ? <button type="button" className="text-button" onClick={showOriginalPacket}>
                <FileCode size={15} aria-hidden="true"/>原始输出报文
              </button> : null}
              {result?.raw_response ? <button type="button" className="text-button"
                onClick={() => setPacket({title:'本次响应报文', filename:'playground-response', body:result.raw_response,
                  is_json:true, description:'所选模型服务此次返回的完整响应正文。'})}>
                <FileCode size={15} aria-hidden="true"/>本次响应报文
              </button> : null}
            </div>
          </div>
          {traceId ? <Tabs id="playground-view" label="调试结果视图" className="ws-tabs playground-tabs"
            items={resultTabs} active={view} onChange={setView}/> : null}
          {error ? <p role="alert" className="playground-panel-note error-inline">{error}</p> : null}
          {running ? <p className="playground-panel-note muted" role="status">等待模型响应…</p> : null}
          <div className="playground-result-panel" role={traceId ? 'tabpanel' : undefined}
            id={traceId ? `playground-view-panel-${view}` : undefined}
            aria-labelledby={traceId ? `playground-view-tab-${view}` : undefined}>
            {view === 'request' ? <div className="playground-response" tabIndex={0}>
              {!source?.parameters.messages.length ? <p className="muted">{loadingSource ? '正在读取原始请求…' : '此轨迹没有可供对比的请求内容。'}</p>
                : <>
                  {result && input !== result.input ? <p className="playground-comparison-note">编辑区有尚未运行的修改。下方显示上次运行的请求。</p> : null}
                  <PlaygroundDiff before={capturedInput} after={result?.input ?? draft}
                    originalLabel={source?.selected_span ? '原始请求' : '带入的输入'} updatedLabel={result ? '本次运行' : '当前编辑'}/>
                </>}
            </div> : view === 'output' ? <div className="playground-response" tabIndex={0}>
              <section className="playground-output-card" aria-labelledby="playground-original-output">
                <h3 id="playground-original-output">原始输出</h3>
                <div className="playground-output-body">
                  {originalOutput ? <ModelOutput message={originalOutput.message}/>
                    : <p className="muted">{loadingSource ? '正在读取原始输出…' : '此调用未采集可供对比的原始输出。'}</p>}
                </div>
              </section>
              <section className="playground-output-card" aria-labelledby="playground-current-output">
                <h3 id="playground-current-output">本次返回</h3>
                <div className="playground-output-body">
                  {result ? <ModelOutput message={result.message}/>
                    : <p className="muted">{running ? '正在等待本次调试结果。' : '运行调试后显示本次返回内容。'}</p>}
                </div>
              </section>
            </div> : <div className="playground-response" role="region" aria-label="模型响应内容" tabIndex={0}>
              {result ? <>
                <dl className="playground-metrics">
                  <div><dt>模型</dt><dd>{result.model}</dd></div>
                  <div><dt>耗时</dt><dd>{seconds.format(result.duration_seconds)} 秒</dd></div>
                  <div><dt>结束原因</dt><dd>{result.finish_reason ?? '—'}</dd></div>
                  <div><dt>输入 Tokens</dt><dd>{result.usage.prompt_tokens == null ? '—' : integers.format(result.usage.prompt_tokens)}</dd></div>
                  <div><dt>输出 Tokens</dt><dd>{result.usage.completion_tokens == null ? '—' : integers.format(result.usage.completion_tokens)}</dd></div>
                </dl>
                <ModelOutput message={result.message}/>
              </> : !running && !error ? <p className="muted">运行调试后显示模型返回内容。</p> : null}
            </div>}
          </div>
        </section>
      </div>
    </div>
    {packet ? <PacketPreview packet={packet} onClose={() => setPacket(null)}/> : null}
  </div>
}
