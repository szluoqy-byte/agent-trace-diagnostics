// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import TaskCreate from './TaskCreate'
import {api} from '../api'
import {WorkspaceContext} from '../workspace'

vi.mock('../api', async importOriginal => ({...await importOriginal<typeof import('../api')>(), api:vi.fn()}))
afterEach(() => {cleanup(); vi.clearAllMocks()})

function renderUpload() {
  render(<WorkspaceContext value={{
    config: null, busy: false, version: 0,
    action: async work => work(), notify: vi.fn(), refresh: vi.fn(), go: vi.fn(), openSettings: vi.fn(),
  }}><TaskCreate source={null}/></WorkspaceContext>)
}

it('accepts both upload formats through the same entry point without offering preset data', async () => {
  vi.mocked(api).mockResolvedValue({id:'preview', files:[
    {id:'0', name:'genai.json', formats:['otel_genai'], count:1, errors:[]},
    {id:'1', name:'native.jsonl', formats:['messages'], count:1, errors:[]},
  ], rows:[
    {id:'otel', file_id:'0', outcome:'failed'}, {id:'native', file_id:'1', outcome:'succeeded'},
  ]})
  renderUpload()
  expect(screen.getByRole('button',{name:'选择文件'})).toBeTruthy()
  expect(screen.getByRole('link',{name:'标准 JSON'}).getAttribute('download')).toBe('')
  expect(screen.getByRole('link',{name:'JSONL 批量'}).getAttribute('href')).toBe('/templates/trajectory-jsonl-template.jsonl')
  expect(screen.getByRole('link',{name:'OTel GenAI'}).getAttribute('href')).toBe('/templates/otel-genai-template.json')
  expect(screen.getByRole('link',{name:'OTel GenAI 批量'}).getAttribute('href')).toBe('/templates/otel-genai-batch-template.json')
  expect(screen.queryByRole('button',{name:/样例|示例|演示/})).toBeNull()
  const files = [new File(['{}'], 'genai.json'), new File(['{}'], 'native.jsonl')]
  fireEvent.change(screen.getByLabelText('选择轨迹文件'), {target:{files}})
  expect(await screen.findByText('genai.json')).toBeTruthy()
  expect(screen.getByText('native.jsonl')).toBeTruthy()
  const [path, request] = vi.mocked(api).mock.calls[0]
  expect(path).toBe('/import/preview')
  expect((request?.body as FormData).getAll('files')).toHaveLength(2)
  expect((screen.getByRole('button',{name:'保存草稿'}) as HTMLButtonElement).disabled).toBe(false)
})

it('identifies an unsupported file and lets the user exclude it before saving', async () => {
  vi.mocked(api).mockResolvedValue({id:'preview', files:[
    {id:'0', name:'parallel.json', formats:[], count:0, errors:['OTel 包含并发 GenAI 分支']},
    {id:'1', name:'native.jsonl', formats:['messages'], count:1, errors:[]},
  ], rows:[{id:'native', file_id:'1', outcome:'failed'}]})
  renderUpload()
  const files = [new File(['{}'], 'parallel.json'), new File(['{}'], 'native.jsonl')]
  fireEvent.change(screen.getByLabelText('选择轨迹文件'), {target:{files}})
  expect(await screen.findByText('OTel 包含并发 GenAI 分支')).toBeTruthy()
  const save = screen.getByRole('button',{name:'保存草稿'}) as HTMLButtonElement
  expect(save.disabled).toBe(true)
  fireEvent.click(screen.getByRole('button',{name:'排除文件 parallel.json'}))
  expect(save.disabled).toBe(false)
})
