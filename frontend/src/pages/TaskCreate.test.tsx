// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import TaskCreate from './TaskCreate'
import {api,post} from '../api'
import {WorkspaceContext} from '../workspace'

vi.mock('../api', async importOriginal => ({...await importOriginal<typeof import('../api')>(), api:vi.fn(), post:vi.fn()}))
afterEach(() => {cleanup(); vi.clearAllMocks()})

function renderUpload() {
  const request = vi.mocked(api).getMockImplementation()
  vi.mocked(api).mockImplementation((path, options) => path === '/model-configurations'
    ? Promise.resolve({items:[
      {id:'default', name:'默认连接', model:'model-default', is_default:true},
      {id:'alternate', name:'备用连接', model:'model-alternate', is_default:false},
    ]} as never)
    : request!(path,options))
  render(<WorkspaceContext value={{
    config: null, busy: false, version: 0,
    action: async work => work(), notify: vi.fn(), refresh: vi.fn(), go: vi.fn(), openSettings: vi.fn(),
  }}><TaskCreate source={null}/></WorkspaceContext>)
}

it('accepts DeepAgent and OTel through the shared uploader', async () => {
  vi.mocked(api).mockResolvedValue({id:'preview', files:[
    {id:'0', name:'genai.json', formats:['otel_genai'], count:1, errors:[]},
    {id:'1', name:'deepagent.json', formats:['deepagent'], count:1, errors:[]},
  ], rows:[
    {id:'otel', file_id:'0', outcome:'failed'},
    {id:'deepagent', file_id:'1', outcome:'failed'},
  ]})
  renderUpload()
  const languages = within(screen.getByRole('group', {name:'提示词语言'})).getAllByRole<HTMLInputElement>('radio')
  expect(languages.map(option => option.value)).toEqual(['zh', 'en'])
  expect(languages.map(option => option.checked)).toEqual([true, false])
  fireEvent.click(languages[1])
  expect(languages.map(option => option.checked)).toEqual([false, true])
  expect(screen.getByRole('button',{name:'选择文件'})).toBeTruthy()
  expect(screen.getByRole('link',{name:'DeepAgent JSON'}).getAttribute('href')).toBe('/templates/deepagent-template.json')
  expect(screen.queryByRole('link',{name:'标准 JSON'})).toBeNull()
  expect(screen.queryByRole('link',{name:'JSONL 批量'})).toBeNull()
  expect(screen.getByRole('link',{name:'OTel GenAI'}).getAttribute('href')).toBe('/templates/otel-genai-template.json')
  expect(screen.getByRole('link',{name:'OTel GenAI 批量'}).getAttribute('href')).toBe('/templates/otel-genai-batch-template.json')
  expect(screen.queryByRole('button',{name:/样例|示例|演示/})).toBeNull()
  const files = [new File(['{}'], 'genai.json'), new File(['{}'], 'deepagent.json')]
  fireEvent.change(screen.getByLabelText('选择轨迹文件'), {target:{files}})
  expect(await screen.findByText('genai.json')).toBeTruthy()
  expect(screen.getByText('deepagent.json')).toBeTruthy()
  const [, request] = vi.mocked(api).mock.calls.find(([path]) => path === '/import/preview')!
  expect((request?.body as FormData).getAll('files')).toHaveLength(2)
  expect((screen.getByRole('button',{name:'保存草稿'}) as HTMLButtonElement).disabled).toBe(false)
})

it('identifies an unsupported file and lets the user exclude it before saving', async () => {
  vi.mocked(api).mockResolvedValue({id:'preview', files:[
    {id:'0', name:'parallel.json', formats:[], count:0, errors:['OTel 包含并发 GenAI 分支']},
    {id:'1', name:'deepagent.json', formats:['deepagent'], count:1, errors:[]},
  ], rows:[{id:'native', file_id:'1', outcome:'failed'}]})
  renderUpload()
  const files = [new File(['{}'], 'parallel.json'), new File(['{}'], 'deepagent.json')]
  fireEvent.change(screen.getByLabelText('选择轨迹文件'), {target:{files}})
  expect(await screen.findByText('OTel 包含并发 GenAI 分支')).toBeTruthy()
  const save = screen.getByRole('button',{name:'保存草稿'}) as HTMLButtonElement
  expect(save.disabled).toBe(true)
  fireEvent.click(screen.getByRole('button',{name:'排除文件 parallel.json'}))
  expect(save.disabled).toBe(false)
})

it('preselects Standard, describes when to use each level, and saves Deep', async () => {
  vi.mocked(api).mockResolvedValue({id:'preview', files:[
    {id:'0', name:'deepagent.json', formats:['deepagent'], count:1, errors:[]},
  ], rows:[{id:'native', file_id:'0', outcome:'failed'}]})
  vi.mocked(post).mockResolvedValue({task_id:'task', run_id:null})
  renderUpload()

  const group = within(screen.getByRole('group', {name:'诊断等级'}))
  const [fast, standard, deep] = group.getAllByRole<HTMLInputElement>('radio')
  expect([fast.value, standard.value, deep.value]).toEqual(['fast', 'standard', 'deep'])
  expect(standard.checked).toBe(true)
  expect(group.getByRole('radio', {name:'Fast 快速诊断', description:/^单路 .*批量轨迹诊断/})).toBe(fast)
  expect(group.getByRole('radio', {name:'Standard 标准诊断', description:/^双路 · 默认推荐 .*常规问题排查/})).toBe(standard)
  expect(group.getByRole('radio', {name:'Deep 深度诊断', description:/^多路 .*复杂长轨迹/})).toBe(deep)
  expect(group.queryByText(/AgentDebugX|CHIEF|TrajDebug|RSI/)).toBeNull()
  expect(screen.getByRole<HTMLInputElement>('checkbox', {name:/自动生成改进建议/}).checked).toBe(false)

  fireEvent.click(deep)
  expect(deep.checked && !standard.checked).toBe(true)
  expect(screen.getByText('Deep · 深度诊断')).toBeTruthy()
  expect(screen.getByRole('checkbox', {name:/自动生成改进建议/})).toBeTruthy()
  expect(screen.queryByLabelText(/Agent 名称|Agent 版本|排查目标/)).toBeNull()
  const modelSelect=screen.getByRole<HTMLSelectElement>('combobox', {name:'诊断模型'})
  await waitFor(() => expect(modelSelect.value).toBe('default'))
  fireEvent.change(modelSelect, {target:{value:'alternate'}})
  expect(screen.getByText('model-alternate', {selector:'dd'})).toBeTruthy()
  fireEvent.change(screen.getByLabelText('选择轨迹文件'), {target:{files:[new File(['{}'], 'deepagent.json')]}})
  await screen.findByRole('status', {name:''})
  await waitFor(() => expect((screen.getByRole('button',{name:'保存草稿'}) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button',{name:'保存草稿'}))
  expect(post).toHaveBeenCalledWith('/tasks', expect.objectContaining({
    diagnosis_mode:'deep', model_connection_id:'alternate', start:false,
  }))
  const [, payload] = vi.mocked(post).mock.calls[0]
  for(const field of ['agent','version','goal'])expect(payload).not.toHaveProperty(field)
  fireEvent.click(screen.getByRole('button',{name:'创建并开始分析'}))
  expect(post).toHaveBeenLastCalledWith('/tasks', expect.objectContaining({model_connection_id:'alternate', start:true}))
})
