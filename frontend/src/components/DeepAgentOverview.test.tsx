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

const rawSource = {
  spans:[{span_id:'llm', span_name:'Model call', span_input:'{"request":"Read status"}', span_output:'{"answer":"Blocked request"}'},
    {span_id:'risk', parent_id:'llm', attributes:'raw telemetry'}],
  nodes:[{span_id:'llm', title:'Model display name', kind:'LLM', duration_us:4800000, status_code:'0', finish_reason:'content-filter',
    start_offset_us:0, end_offset_us:4800000, depth:0},
    {span_id:'risk', title:'Safety check', kind:'RISK_CHECK', duration_us:29740, status_code:'1', finish_reason:null,
      start_offset_us:1200000, end_offset_us:1229740, depth:1}],
  message_sources:[{step:0, span_id:'llm', field:'span'}],
}

it('shows a single call with duration, recorded input, output and status fields', async () => {
  vi.mocked(api).mockResolvedValue(rawSource)
  const {rerender} = render(<DeepAgentOverview trace={trace} criticalStep={0} selection={null}/>)
  expect(screen.getByText('2 个调用')).toBeTruthy()
  expect(screen.getByRole('link').getAttribute('href')).toBe('/api/traces/trace/deepagent?download=true')
  fireEvent.click(screen.getByRole('tab', {name:'调用列表'}))
  expect((await screen.findByText('#1 Model call')).closest('details')).toBeTruthy()
  expect(screen.getByText('#2 Safety check')).toBeTruthy()
  expect(screen.getByText('4.8 s')).toBeTruthy()
  expect(screen.getByText('29.74 ms')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', {name:'筛选调用'}), {target:{value:'Safety check'}})
  expect(screen.queryByText('#1 Model call')).toBeNull()
  expect(screen.getByText('#2 Safety check')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', {name:'调用时序'}))
  expect(screen.getByRole('tabpanel', {name:'调用时序'})).toBeTruthy()

  rerender(<DeepAgentOverview trace={trace} criticalStep={0} selection={{step:0, quote:'Blocked request'}}/>)
  const record = (await screen.findByText('#1 Model call')).closest('details')!
  expect(record.open).toBe(true)
  expect(within(record).getByLabelText('输入').textContent).toContain('Read status')
  expect(within(record).getByLabelText('输出').textContent).toContain('Blocked request')
  expect(within(record).getByText('Span 状态码')).toBeTruthy()
  expect(within(record).getByText('content-filter')).toBeTruthy()
  expect(screen.getByRole('tabpanel', {name:'调用列表'})).toBeTruthy()
  expect(within(record).getByText('诊断内容')).toBeTruthy()
  expect(within(record).getByText('算法步骤 #0')).toBeTruthy()
  expect(record.querySelector('mark')?.textContent).toBe('Blocked request')
})

it('defaults to the first tab, the timeline, and opens calls by their original number', async () => {
  vi.mocked(api).mockResolvedValue(rawSource)
  render(<DeepAgentOverview trace={trace} criticalStep={0} selection={null}/>)
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['调用时序', '调用列表'])
  expect(screen.getByRole('tab', {name:'调用时序'}).getAttribute('aria-selected')).toBe('true')
  const timeline = screen.getByRole('tabpanel', {name:'调用时序'})
  expect(await within(timeline).findByRole('button', {name:/#1 Model call.*关键调用/})).toBeTruthy()
  const child = within(timeline).getByRole('button', {name:/#2 Safety check.*开始于 \+1.2 s.*29.74 ms/})
  expect(child.querySelector('.deepagent-waterfall-bar')).not.toBeNull()
  fireEvent.click(child)
  const list = screen.getByRole('tabpanel', {name:'调用列表'})
  expect(within(list).getByText('#2 Safety check').closest('details')?.open).toBe(true)
  expect(within(list).getByText('父调用 Model call')).toBeTruthy()
})
