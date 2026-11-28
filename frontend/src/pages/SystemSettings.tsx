import {Fragment, useState} from 'react'
import type {FormEvent} from 'react'
import {ArrowLeft, Plus} from 'lucide-react'
import {api, post, put} from '../api'
import {Dialog} from '../components/Dialogs'
import Tabs from '../components/Tabs'
import {Badge, useResource, useWorkspace} from '../workspace'

const modelTabs = [
  {id:'diagnosis', label:'诊断分析模型配置'},
  {id:'playground', label:'Playground 模型配置'},
] as const
type ModelPurpose = typeof modelTabs[number]['id']

const runtimeFields = [
  {id:'concurrency', label:'轨迹并发上限', min:1, max:8, help:'同时处理的诊断作业数量。'},
  {id:'llm_concurrency', label:'模型请求并发上限', min:1, max:16, help:'诊断、综合分析、改进建议和连接测试共用。'},
  {id:'max_retries', label:'请求重试次数', min:0, max:5, help:'模型请求失败后的自动重试次数。'},
  {id:'timeout', label:'请求超时（秒）', min:5, max:600, help:'单次模型请求的最长等待时间。'},
  {id:'max_tokens', label:'单次输出上限', min:1024, max:131072, help:'单次模型请求允许生成的最大 token 数。'},
] as const
type RuntimeNumber = typeof runtimeFields[number]['id']
type RuntimeSettings = Record<RuntimeNumber, number> & {cluster_json_mode:boolean}
type RuntimeDraft = Record<RuntimeNumber, string> & {cluster_json_mode:boolean}

interface ModelConfiguration {
  id: string
  name: string
  base_url: string
  model: string
  has_key: boolean
  has_headers: boolean
  is_default: boolean
  runtime?: RuntimeSettings
}

interface TestResult {
  ok: boolean
  message: string
}

function modelPath(purpose: ModelPurpose, suffix = '') {
  return '/model-configurations' + suffix + (purpose === 'playground' ? '?purpose=playground' : '')
}

function runtimeDraft(runtime?: RuntimeSettings, defaults?: RuntimeSettings): RuntimeDraft {
  return {
    concurrency: runtime ? String(runtime.concurrency) : '',
    llm_concurrency: runtime ? String(runtime.llm_concurrency) : '',
    max_retries: runtime ? String(runtime.max_retries) : '',
    timeout: runtime ? String(runtime.timeout) : '',
    max_tokens: runtime ? String(runtime.max_tokens) : '',
    cluster_json_mode: runtime?.cluster_json_mode ?? defaults?.cluster_json_mode ?? false,
  }
}

function runtimePayload(value: RuntimeDraft) {
  return {
    ...Object.fromEntries(runtimeFields.map(({id}) => [id, value[id] === '' ? null : Number(value[id])])),
    cluster_json_mode:value.cluster_json_mode,
  }
}

export default function SystemSettings({onBack, initialPurpose = 'diagnosis'}: {onBack?: () => void; initialPurpose?: ModelPurpose}) {
  const [purpose, setPurpose] = useState<ModelPurpose>(initialPurpose)
  return <>
    <div className="ws-heading">
      <div><h1>系统设置</h1><p>管理诊断分析与 Playground 使用的模型连接。</p></div>
      {onBack ? <button className="secondary" onClick={onBack}><ArrowLeft size={15}/>返回上一页</button> : null}
    </div>
    <Tabs id="settings-model" label="模型用途" className="ws-tabs settings-model-tabs"
      items={[...modelTabs]} active={purpose} onChange={id=>setPurpose(id === 'playground' ? 'playground' : 'diagnosis')}/>
    <div key={purpose} className="ws-settings-body ws-content-scroll" role="tabpanel"
      id={`settings-model-panel-${purpose}`} aria-labelledby={`settings-model-tab-${purpose}`}>
      <ModelConfigurations purpose={purpose}/>
    </div>
  </>
}

function ModelConfigurations({purpose}: {purpose:ModelPurpose}) {
  const {version, busy, action, refresh} = useWorkspace()
  const {data, error} = useResource<{items:ModelConfiguration[]; runtime_defaults?:RuntimeSettings}>(modelPath(purpose), version)
  const [editing, setEditing] = useState<ModelConfiguration | 'new' | null>(null)
  const [testing, setTesting] = useState('')
  const [results, setResults] = useState<Record<string, TestResult>>({})
  const diagnosis = purpose === 'diagnosis'
  const close = () => setEditing(null)

  async function test(id: string) {
    setTesting(id)
    setResults(old => ({...old, [id]: {ok:true, message:'正在测试连接…'}}))
    try {
      await post(modelPath(purpose, `/${id}/test`))
      setResults(old => ({...old, [id]: {ok:true, message:'连接正常，模型已返回有效 JSON。'}}))
    } catch (error) {
      setResults(old => ({...old, [id]: {ok:false, message:(error as Error).message}}))
    } finally {
      setTesting('')
    }
  }

  return <>
    <section className="ws-surface model-settings" aria-label={diagnosis ? '诊断分析模型列表' : 'Playground 模型列表'}>
      <div className="model-settings-heading">
        <p>{diagnosis ? '默认诊断模型用于新任务，并提供工作台的并发上限。' : '为后续轨迹回放保存独立的模型连接。'}</p>
        <button className="primary" onClick={()=>setEditing('new')} disabled={!data || busy}><Plus size={16}/>添加模型配置</button>
      </div>
      {error ? <div className="model-settings-empty" role="alert">{error} <button className="text-button" onClick={refresh}>重新加载</button></div> : null}
      {data?.items.length ? <ul className="model-connections">
        {data.items.map(item => {
          const result = results[item.id]
          const environment = item.id === 'environment'
          const runtime = item.runtime
          return <li key={item.id} className="model-connection">
            <div className="model-connection-details">
              <div className="model-connection-title"><h2>{item.name}</h2>
                {item.is_default ? <Badge value="completed">默认模型</Badge> : null}
              </div>
              <dl>
                <dt>模型标识</dt><dd>{item.model}</dd>
                <dt>接口地址</dt><dd>{item.base_url}</dd>
                <dt>API 密钥</dt><dd>{item.has_key ? '已配置' : '未配置'}</dd>
                <dt>自定义 Header</dt><dd>{item.has_headers ? '已配置' : '未配置'}</dd>
              </dl>
              {runtime ? <details className="model-runtime-summary">
                <summary>运行参数</summary>
                <dl>{runtimeFields.map(({id, label})=><Fragment key={id}><dt>{label}</dt><dd>{runtime[id]}</dd></Fragment>)}
                  <dt>聚类 JSON 模式</dt><dd>{runtime.cluster_json_mode ? '已开启' : '已关闭'}</dd>
                </dl>
              </details> : null}
              {result ? <p className={result.ok ? 'muted' : 'error-inline'} role={result.ok ? 'status' : 'alert'}>{result.message}</p> : null}
            </div>
            <div className="ws-actions model-connection-actions">
              <button className="secondary small" disabled={busy || !!testing} onClick={()=>void test(item.id)}>
                {testing === item.id ? '正在测试…' : '测试连接'}
              </button>
              {!item.is_default ? <button className="secondary small" disabled={busy || !!testing}
                onClick={()=>void action(() => post(modelPath(purpose, `/${item.id}/default`)), '已更新默认模型')}>设为默认</button> : null}
              <button className="secondary small" disabled={busy || !!testing} onClick={()=>setEditing(item)}>
                {environment ? '编辑运行参数' : '编辑'}
              </button>
              {!environment ? <button className="text-button" disabled={busy || !!testing}
                onClick={()=>{if(window.confirm(`确定删除模型配置“${item.name}”吗？`)) void action(
                  ()=>api(modelPath(purpose, `/${item.id}`), {method:'DELETE'}), '已删除模型配置')}}>删除</button> : null}
            </div>
          </li>
        })}
      </ul> : !error ? <p className="model-settings-empty" role="status">{data ? '暂无模型配置。' : '正在加载模型配置…'}</p> : null}
    </section>
    {editing && editing !== 'new' && editing.id === 'environment' && editing.runtime ? (
      <EnvironmentRuntimeForm runtime={editing.runtime} onClose={close}/>
    ) : editing ? <ModelConfigurationForm purpose={purpose} defaults={data?.runtime_defaults}
      configuration={editing === 'new' ? undefined : editing} onClose={close} first={!data?.items.length}/> : null}
  </>
}

function RuntimeFields({value, defaults, onChange, disabled, required = false}: {
  value:RuntimeDraft; defaults?:RuntimeSettings; onChange:(value:RuntimeDraft)=>void; disabled:boolean; required?:boolean
}) {
  return <fieldset className="model-runtime-fields" disabled={disabled}>
    <legend>运行参数</legend>
    <div className="runtime-settings-grid">
      {runtimeFields.map(field => <label key={field.id}>{field.label}
        <input type="number" min={field.min} max={field.max} step={field.id === 'timeout' ? 'any' : 1}
          required={required} placeholder={defaults ? `默认 ${defaults[field.id]}` : undefined} value={value[field.id]}
          onChange={event=>onChange({...value, [field.id]:event.target.value})}/>
        <small>{field.help}</small>
      </label>)}
      <label className="ws-check runtime-json-mode"><input type="checkbox" checked={value.cluster_json_mode}
        onChange={event=>onChange({...value, cluster_json_mode:event.target.checked})}/>
        <span>强制聚类阶段使用 JSON<small>适用于要求 JSON 响应格式的模型服务。</small></span></label>
    </div>
  </fieldset>
}

function EnvironmentRuntimeForm({runtime, onClose}: {runtime:RuntimeSettings; onClose:()=>void}) {
  const {busy, action} = useWorkspace()
  const [value, setValue] = useState(()=>runtimeDraft(runtime))
  const [error, setError] = useState('')
  async function save(event:FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved = await action(()=>put('/runtime-settings?scope=environment', runtimePayload(value)), '已更新环境配置运行参数', setError)
    if (saved) onClose()
  }
  return <Dialog title="编辑环境配置运行参数" onClose={onClose} busy={busy}>
    <form onSubmit={save}>
      <div className="modal-body model-configuration-form">
        <RuntimeFields value={value} onChange={setValue} disabled={busy} required/>
        {error ? <p role="alert" className="error-inline">{error}</p> : null}
      </div>
      <footer><button type="button" className="secondary" onClick={onClose} disabled={busy}>取消</button>
        <button type="submit" className="primary" disabled={busy}>{busy ? '正在保存…' : '保存配置'}</button></footer>
    </form>
  </Dialog>
}

function parseHeaders(text: string): Record<string, string> {
  const value = JSON.parse(text || '{}')
  if (!value || Array.isArray(value) || typeof value !== 'object' || Object.values(value).some(item => typeof item !== 'string')) {
    throw new Error('自定义 Header 必须是值为字符串的 JSON 对象。')
  }
  return value
}

function ModelConfigurationForm({purpose, first, configuration, defaults, onClose}: {
  purpose:ModelPurpose; first:boolean; configuration?:ModelConfiguration; defaults?:RuntimeSettings; onClose:()=>void
}) {
  const {busy, action} = useWorkspace()
  const diagnosis = purpose === 'diagnosis'
  const [value, setValue] = useState({name:configuration?.name ?? '', base_url:configuration?.base_url ?? '',
    model:configuration?.model ?? '', api_key:'', headers:''})
  const [runtime, setRuntime] = useState(()=>runtimeDraft(configuration?.runtime, defaults))
  const [makeDefault, setMakeDefault] = useState(configuration?.is_default ?? first)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [saveError, setSaveError] = useState('')
  const locked = busy || testing

  function change(field: keyof typeof value, text: string) {
    setValue(old => ({...old, [field]:text}))
    setResult(null)
    setSaveError('')
  }

  async function test(form: HTMLFormElement | null) {
    if (locked || (!configuration && !form?.reportValidity())) return
    setTesting(true)
    setResult(null)
    try {
      if (configuration) {
        await post(modelPath(purpose, `/${configuration.id}/test`))
      } else {
        await post(modelPath(purpose, '/test'), {...value, headers:parseHeaders(value.headers.trim())})
      }
      setResult({ok:true, message:'连接正常，模型已返回有效 JSON。'})
    } catch (error) {
      setResult({ok:false, message:error instanceof SyntaxError ? '自定义 Header 必须是有效的 JSON 对象。' : (error as Error).message})
    } finally {
      setTesting(false)
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (locked) return
    setSaveError('')
    const {headers, ...connection} = value
    const payload: Record<string, unknown> = {...connection, make_default:makeDefault}
    try {
      if (headers.trim()) payload.headers = parseHeaders(headers.trim())
    } catch {
      setSaveError('自定义 Header 必须是值为字符串的 JSON 对象。')
      return
    }
    if (diagnosis) payload.runtime = runtimePayload(runtime)
    const saved = await action(
      () => configuration ? put(modelPath(purpose, `/${configuration.id}`), payload) : post(modelPath(purpose), payload),
      configuration ? '已更新模型配置' : '已保存模型配置',
      setSaveError,
    )
    if (saved) onClose()
  }

  return <Dialog title={configuration ? '编辑模型配置' : '添加模型配置'} onClose={onClose} busy={locked}>
    <form onSubmit={save}>
      <div className="modal-body model-configuration-form">
        <label>配置名称<input required autoFocus maxLength={100} value={value.name}
          disabled={locked} onChange={event=>change('name', event.target.value)}/></label>
        <label>接口地址<input required type="url" maxLength={2048} value={value.base_url}
          placeholder="http://127.0.0.1:8317/v1" aria-describedby="model-base-help" disabled={locked}
          onChange={event=>change('base_url', event.target.value)}/></label>
        <p className="ws-help" id="model-base-help">填写兼容 OpenAI Chat Completions 的接口地址，保留服务要求的路径。</p>
        <label>模型标识<input required maxLength={200} value={value.model} disabled={locked}
          onChange={event=>change('model', event.target.value)}/></label>
        <label>API 密钥<input type="password" autoComplete="new-password" maxLength={4096}
          placeholder={configuration ? '留空保持原密钥' : '无需鉴权时留空'}
          value={value.api_key} disabled={locked} onChange={event=>change('api_key', event.target.value)}/></label>
        <label>自定义 Header<textarea rows={3} value={value.headers} aria-describedby="model-headers-help" disabled={locked}
          placeholder={'{"X-API-Key":"your-key"}'} onChange={event=>change('headers', event.target.value)}/></label>
        <p className="ws-help" id="model-headers-help">{configuration ? '留空保留原 Header，填写 {} 可清空。' : '填写以字符串为值的 JSON 对象。'}</p>
        <label className="ws-check"><input type="checkbox" checked={makeDefault} disabled={locked}
          onChange={event=>setMakeDefault(event.target.checked)}/>{diagnosis ? '设为默认诊断模型' : '设为默认 Playground 模型'}</label>
        <div className="ws-actions model-form-test">
          <button type="button" className="secondary small" disabled={locked}
            onClick={event=>void test(event.currentTarget.form)}>
            {testing ? '正在测试…' : configuration ? '测试已保存配置' : '测试连接'}
          </button>
        </div>
        {result ? <p role={result.ok ? 'status' : 'alert'} className={result.ok ? 'muted' : 'error-inline'}>{result.message}</p> : null}
        {diagnosis ? <RuntimeFields value={runtime} defaults={defaults} onChange={setRuntime} disabled={locked}/> : null}
        {saveError ? <p role="alert" className="error-inline">{saveError}</p> : null}
      </div>
      <footer><button type="button" className="secondary" onClick={onClose} disabled={locked}>取消</button>
        <button type="submit" className="primary" disabled={locked}>{busy ? '正在保存…' : '保存配置'}</button></footer>
    </form>
  </Dialog>
}
