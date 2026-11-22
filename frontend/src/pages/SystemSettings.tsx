import {useCallback, useState} from 'react'
import type {FormEvent} from 'react'
import {ArrowLeft, Plus} from 'lucide-react'
import {post} from '../api'
import {Dialog} from '../components/Dialogs'
import {Badge, useResource, useWorkspace} from '../workspace'

const path = '/model-configurations'

interface ModelConfiguration {
  id: string
  name: string
  base_url: string
  model: string
  has_key: boolean
  is_default: boolean
}

interface TestResult {
  ok: boolean
  message: string
}

export default function SystemSettings({onBack}: {onBack?: () => void}) {
  const {config, version, busy, action, refresh} = useWorkspace()
  const {data, error} = useResource<{items: ModelConfiguration[]}>(path, version)
  const [adding, setAdding] = useState(false)
  const [testing, setTesting] = useState('')
  const [results, setResults] = useState<Record<string, TestResult>>({})
  const close = useCallback(() => setAdding(false), [])

  async function test(id: string) {
    setTesting(id)
    setResults(old => ({...old, [id]: {ok: true, message: '正在测试连接…'}}))
    try {
      await post(`${path}/${id}/test`)
      setResults(old => ({...old, [id]: {ok: true, message: '连接正常，模型已返回有效 JSON。'}}))
    } catch (error) {
      setResults(old => ({...old, [id]: {ok: false, message: (error as Error).message}}))
    } finally {
      setTesting('')
    }
  }

  return <>
    <div className="ws-heading">
      <div><h1>系统设置</h1><p>管理诊断与改进建议使用的模型连接。</p></div>
      {onBack ? <button className="secondary" onClick={onBack}><ArrowLeft size={15}/>返回上一页</button> : null}
    </div>
    <section className="ws-surface model-settings" aria-labelledby="model-settings-heading">
      <div className="model-settings-heading">
        <div><h2 id="model-settings-heading">模型配置</h2><p>新启动的分析使用默认模型。已有分析保留各自的配置。</p></div>
        <button className="primary" onClick={() => setAdding(true)} disabled={!data || busy}><Plus size={16}/>添加模型配置</button>
      </div>
      {error ? <div className="model-settings-empty" role="alert">{error} <button className="text-button" onClick={refresh}>重新加载</button></div> : null}
      {data?.items.length ? <ul className="model-connections">
        {data.items.map(item => {
          const result = results[item.id]
          return <li key={item.id} className="model-connection">
            <div className="model-connection-details">
              <div className="model-connection-title"><h3>{item.name}</h3>
                {item.is_default ? <Badge value="completed">默认模型</Badge> : null}
              </div>
              <dl>
                <dt>模型标识</dt><dd>{item.model}</dd>
                <dt>接口地址</dt><dd>{item.base_url}</dd>
                <dt>API 密钥</dt><dd>{item.has_key ? '已配置' : '未配置'}</dd>
              </dl>
              {result ? <p className={result.ok ? 'muted' : 'error-inline'} role={result.ok ? 'status' : 'alert'}>{result.message}</p> : null}
            </div>
            <div className="ws-actions model-connection-actions">
              <button className="secondary small" disabled={busy || !!testing} onClick={() => void test(item.id)}>
                {testing === item.id ? '正在测试…' : '测试连接'}
              </button>
              {item.is_default ? null : <button className="secondary small" disabled={busy || !!testing}
                onClick={() => void action(() => post(`${path}/${item.id}/default`), '已更新默认诊断模型')}>设为默认</button>}
            </div>
          </li>
        })}
      </ul> : !error ? <p className="model-settings-empty" role="status">{data ? '暂无模型配置。添加配置并设为默认后即可开始诊断。' : '正在加载模型配置…'}</p> : null}
    </section>
    {config ? <section className="ws-surface ws-config model-runtime"><h2>运行参数</h2><dl className="model-runtime-values">
      <dt>轨迹并发上限</dt><dd>{config.concurrency}</dd><dt>步骤分析并发上限</dt><dd>{config.llm_concurrency ?? '—'}</dd>
    </dl></section> : null}
    {adding ? <ModelConfigurationForm onClose={close} first={!data?.items.length}/> : null}
  </>
}

function ModelConfigurationForm({first, onClose}: {first: boolean; onClose: () => void}) {
  const {busy, action} = useWorkspace()
  const [value, setValue] = useState({name: '', base_url: '', model: '', api_key: ''})
  const [makeDefault, setMakeDefault] = useState(first)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [saveError, setSaveError] = useState('')
  const locked = busy || testing

  function change(field: keyof typeof value, text: string) {
    setValue(old => ({...old, [field]: text}))
    setResult(null)
    setSaveError('')
  }

  async function test(form: HTMLFormElement | null) {
    if (locked || !form?.reportValidity()) return
    setTesting(true)
    setResult(null)
    try {
      await post(path + '/test', value)
      setResult({ok: true, message: '连接正常，模型已返回有效 JSON。'})
    } catch (error) {
      setResult({ok: false, message: (error as Error).message})
    } finally {
      setTesting(false)
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (locked) return
    setSaveError('')
    const saved = await action(
      () => post<{id: string}>(path, {...value, make_default: makeDefault}),
      makeDefault ? '已保存并设为默认诊断模型' : '已保存模型配置',
      setSaveError,
    )
    if (saved) onClose()
  }

  return <Dialog title="添加模型配置" onClose={onClose} busy={locked}>
    <form onSubmit={save}>
      <div className="modal-body model-configuration-form">
        <label>配置名称<input required autoFocus maxLength={100} value={value.name}
          disabled={locked} onChange={event => change('name', event.target.value)}/></label>
        <label>接口地址<input required type="url" maxLength={2048} value={value.base_url}
          placeholder="http://127.0.0.1:8317/v1" aria-describedby="model-base-help" disabled={locked}
          onChange={event => change('base_url', event.target.value)}/></label>
        <p className="ws-help" id="model-base-help">填写兼容 OpenAI Chat Completions 的接口地址，保留服务要求的路径。</p>
        <label>模型标识<input required maxLength={200} value={value.model} disabled={locked}
          onChange={event => change('model', event.target.value)}/></label>
        <label>API 密钥<input type="password" autoComplete="new-password" maxLength={4096}
          value={value.api_key} aria-describedby="model-key-help" disabled={locked}
          onChange={event => change('api_key', event.target.value)}/></label>
        <p className="ws-help" id="model-key-help">无需鉴权的服务可留空。密钥仅保存在本机服务端。</p>
        <label className="ws-check"><input type="checkbox" checked={makeDefault} disabled={locked}
          onChange={event => setMakeDefault(event.target.checked)}/>设为默认诊断模型</label>
        <div className="ws-actions model-form-test">
          <button type="button" className="secondary small" disabled={locked}
            onClick={event => void test(event.currentTarget.form)}>{testing ? '正在测试…' : '测试连接'}</button>
          <span className="muted">测试仅发送一条固定消息。</span>
        </div>
        {result ? <p role={result.ok ? 'status' : 'alert'} className={result.ok ? 'muted' : 'error-inline'}>{result.message}</p> : null}
        {saveError ? <p role="alert" className="error-inline">{saveError}</p> : null}
      </div>
      <footer><button type="button" className="secondary" onClick={onClose} disabled={locked}>取消</button>
        <button type="submit" className="primary" disabled={locked}>{busy ? '正在保存…' : '保存配置'}</button></footer>
    </form>
  </Dialog>
}
