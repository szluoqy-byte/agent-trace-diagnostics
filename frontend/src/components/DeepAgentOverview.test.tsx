// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react'
import {api} from '../api'
import type {Trace} from '../types'
import DeepAgentOverview from './DeepAgentOverview'

vi.mock('../api', () => ({api: vi.fn()}))
afterEach(() => {cleanup(); vi.resetAllMocks()})

const trace: Trace = {id:'trace', data:{title:'DeepAgent', task_description:'Read status',
  annotation:{}, source:'agent.json', kind:'imported', outcome:'failed', input_format:'deepagent',
  deepagent:{trace_id:'source-trace', span_count:2},
  messages:[{step:0, role:'assistant', name:'', content:'[Output]\nBlocked request', source_step:'llm:span'}],
}}

it('shows a single call with duration, recorded input, output and status fields', async () => {
  vi.mocked(api).mockResolvedValue({
    spans:[{span_id:'llm', span_input:'{"request":"Read status"}', span_output:'{"answer":"Blocked request"}'},
      {span_id:'risk', parent_id:'llm', attributes:'raw telemetry'}],
    nodes:[{span_id:'llm', title:'Model call', kind:'LLM', duration_us:4800000, status_code:'0', finish_reason:'content-filter'},
      {span_id:'risk', title:'Safety check', kind:'RISK_CHECK', duration_us:29740, status_code:'1', finish_reason:null}],
    message_sources:[{step:0, span_id:'llm', field:'span'}],
  })
  const {rerender} = render(<DeepAgentOverview trace={trace} criticalStep={0} selection={null}/>)
  expect(screen.getByText('2 个调用')).toBeTruthy()
  expect(screen.getByRole('link').getAttribute('href')).toBe('/api/traces/trace/deepagent?download=true')
  expect((await screen.findByText('Model call')).closest('details')).toBeTruthy()
  expect(screen.getByText('4.8 s')).toBeTruthy()
  expect(screen.getByText('29.74 ms')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', {name:'筛选调用'}), {target:{value:'Safety check'}})
  expect(screen.queryByText('Model call')).toBeNull()

  rerender(<DeepAgentOverview trace={trace} criticalStep={0} selection={{step:0, quote:'Blocked request'}}/>)
  const record = (await screen.findByText('Model call')).closest('details')!
  expect(record.open).toBe(true)
  expect(within(record).getByLabelText('输入').textContent).toContain('Read status')
  expect(within(record).getByLabelText('输出').textContent).toContain('Blocked request')
  expect(within(record).getByText('Span 状态码')).toBeTruthy()
  expect(within(record).getByText('content-filter')).toBeTruthy()
  expect(within(record).getByText('诊断内容 #0')).toBeTruthy()
  expect(record.querySelector('mark')?.textContent).toBe('Blocked request')
})
