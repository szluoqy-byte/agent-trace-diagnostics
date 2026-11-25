// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react'
import {api} from '../api'
import type {Trace} from '../types'
import ExecutionTrace from './ExecutionTrace'

vi.mock('../api', () => ({api: vi.fn()}))
afterEach(() => {cleanup(); vi.resetAllMocks()})

const trace: Trace = {id:'trace', data:{title:'DeepAgent', task_description:'Read status',
  annotation:{}, source:'agent.json', kind:'imported', outcome:'failed', input_format:'deepagent',
  deepagent:{trace_id:'source-trace', span_count:2},
  messages:[{step:0, role:'assistant', name:'', content:'Blocked request', source_step:'llm:span'}],
}}

const source = {
  calls:[
    {span_id:'llm', title:'Model call', kind:'LLM', parent_id:null, duration_us:4800000,
      status_code:'0', finish_reason:'content-filter', start_offset_us:0, end_offset_us:4800000, depth:0,
      input:{request:'Read status'}, output:{answer:'Blocked request'}, raw:{span_id:'llm'}},
    {span_id:'risk', title:'Safety check', kind:'RISK_CHECK', parent_id:'llm', duration_us:29740,
      status_code:'1', finish_reason:null, start_offset_us:1200000, end_offset_us:1229740, depth:1,
      input:null, output:null, raw:{span_id:'risk'}},
  ],
  message_sources:[{step:0, span_id:'llm', field:'span', primary:true}],
}

it('opens the diagnosis quote first and follows the source to the captured call', async () => {
  vi.mocked(api).mockResolvedValue(source)
  const {rerender} = render(<ExecutionTrace trace={trace} criticalStep={0} selection={null}/>)
  expect((await screen.findByRole('tab', {name:'调用时序'})).getAttribute('aria-selected')).toBe('true')
  expect(screen.getByRole('link').getAttribute('href')).toBe('/api/traces/trace/deepagent?download=true')

  rerender(<ExecutionTrace trace={trace} criticalStep={0} selection={{step:0, quote:'Blocked request'}}/>)
  const steps = screen.getByRole('tabpanel', {name:'诊断步骤'})
  expect(steps.querySelector('mark')?.textContent).toBe('Blocked request')
  fireEvent.click(within(steps).getByRole('button', {name:'查看来源调用'}))
  const record = (await screen.findByText('#1 Model call')).closest('details')!
  expect(record.open).toBe(true)
  expect(within(record).getByLabelText('输入').textContent).toContain('Read status')
  expect(within(record).getByLabelText('输出').textContent).toContain('Blocked request')
  expect(within(record).getByText('Span 状态码')).toBeTruthy()
  expect(within(record).getByText('content-filter')).toBeTruthy()
  fireEvent.click(within(record).getByRole('button', {name:'诊断步骤 #0'}))
  expect(screen.getByRole('tabpanel', {name:'诊断步骤'}).querySelector('mark')?.textContent).toBe('Blocked request')
})

it.each(['deepagent', 'otel_genai'])('uses the same timeline and call list for %s', async format => {
  vi.mocked(api).mockResolvedValue(source)
  const data: Trace['data'] = format === 'deepagent' ? trace.data : {...trace.data, input_format:undefined,
    deepagent:undefined, otel:{trace_id:'otel', adapter_version:'v1', spans:[], warnings:['采集提示']}}
  render(<ExecutionTrace trace={{...trace, data}} criticalStep={0} selection={null}/>)
  const timeline = await screen.findByRole('tabpanel', {name:'调用时序'})
  expect(vi.mocked(api).mock.calls[0][0]).toBe('/traces/trace/execution')
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['调用时序', '调用列表', '诊断步骤'])
  expect(within(timeline).getByRole('button', {name:/#1 Model call.*关键调用/})).toBeTruthy()
  const child = within(timeline).getByRole('button', {name:/#2 Safety check.*开始于 \+1.2 s.*29.74 ms/})
  expect(child.querySelector('.execution-waterfall-bar')).not.toBeNull()
  fireEvent.click(child)
  const list = screen.getByRole('tabpanel', {name:'调用列表'})
  expect(within(list).getByText('#2 Safety check').closest('details')?.open).toBe(true)
  expect(within(list).getByText('父调用 Model call')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', {name:'筛选调用'}), {target:{value:'Safety check'}})
  expect(within(list).queryByText('#1 Model call')).toBeNull()
  expect(within(list).getByText('#2 Safety check')).toBeTruthy()
})

it('shows the call list when timestamps are unavailable, and only steps for legacy message logs', async () => {
  vi.mocked(api).mockResolvedValue({...source, calls:source.calls.map(call => ({...call, start_offset_us:null, end_offset_us:null}))})
  const first = render(<ExecutionTrace trace={trace} criticalStep={null} selection={null}/>)
  expect((await screen.findByRole('tab', {name:'调用列表'})).getAttribute('aria-selected')).toBe('true')
  expect(screen.queryByRole('tab', {name:'调用时序'})).toBeNull()
  first.unmount()
  vi.mocked(api).mockClear()
  render(<ExecutionTrace trace={{...trace, data:{...trace.data, deepagent:undefined, input_format:undefined}}} criticalStep={null} selection={null}/>)
  expect(screen.getByRole('heading', {name:'诊断步骤'})).toBeTruthy()
  expect(screen.getByText('Blocked request')).toBeTruthy()
  expect(screen.queryByRole('tablist')).toBeNull()
  expect(api).not.toHaveBeenCalled()
})
