// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {api} from '../api'
import type {Trace} from '../types'
import DeepAgentOverview from './DeepAgentOverview'

vi.mock('../api', () => ({api: vi.fn()}))
afterEach(() => {cleanup(); vi.resetAllMocks()})

const trace: Trace = {id:'trace', data:{title:'DeepAgent', task_description:'Read status',
  annotation:{}, source:'agent.json', kind:'imported', outcome:'failed', input_format:'deepagent',
  deepagent:{trace_id:'source-trace', span_count:2},
  messages:[{step:0, role:'assistant', name:'', content:'Recorded answer', source_step:'llm:span_output'}],
}}

it('loads original spans on demand and displays those without diagnostic steps', async () => {
  vi.mocked(api).mockResolvedValue({
    spans:[{span_id:'llm', ai_span_kind:'LLM'}, {span_id:'risk', ai_span_kind:'RISK_CHECK', attributes:'raw telemetry'}],
    message_sources:[{step:0, span_id:'llm', field:'span_output'}],
  })
  render(<DeepAgentOverview trace={trace}/>)
  expect(screen.getByText('2 个原始 span · 1 条诊断消息')).toBeTruthy()
  expect(screen.getByRole('link').getAttribute('href')).toBe('/api/traces/trace/deepagent?download=true')
  expect(api).not.toHaveBeenCalled()
  const panel = screen.getByText('查看全部原始 span').closest('details')!
  panel.open = true
  fireEvent(panel, new Event('toggle'))
  expect(await screen.findByText('诊断步骤 #0')).toBeTruthy()
  const record = screen.getByText('RISK_CHECK').closest('details')!
  record.open = true
  fireEvent(record, new Event('toggle'))
  expect((await screen.findByLabelText('原始 span risk')).textContent).toContain('raw telemetry')
})
