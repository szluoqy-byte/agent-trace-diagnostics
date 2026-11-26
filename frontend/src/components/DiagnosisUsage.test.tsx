// @vitest-environment jsdom
import {cleanup, render, screen, within} from '@testing-library/react'
import {afterEach, expect, it} from 'vitest'
import type {Report} from '../types'
import DiagnosisUsage from './DiagnosisUsage'

afterEach(cleanup)
const report: Report = {algorithm:'TrajDebug', engine_version:'official', mode:'live', model:'fixture', triggers:[], instances:[]}

it('shows one usage row per engine and separates the final assessment', () => {
  render(<DiagnosisUsage report={{...report, duration_seconds:4.2, usage:{requests:8,prompt_tokens:4000,completion_tokens:500},
    comparison:{state:'different',primary:'trajdebug'}, engine_runs:{
      trajdebug:{status:'completed',seconds:4.3},
      agentdebugx:{status:'completed',seconds:6.2,report:{...report,algorithm:'AgentDebugX',
        usage:{requests:3,request_unit:'completion_calls',prompt_tokens:800,completion_tokens:200}}},
    }, synthesis:{status:'completed',summary:'结论',critical_step:1,preferred_engine:'trajdebug',model:'fixture',calls:1,
      requests:2,duration_seconds:1.5,prompt_tokens:200,completion_tokens:50,evidence:[]},
  }}/>)
  const rows = screen.getAllByRole('row')
  expect(rows).toHaveLength(4)
  expect(within(rows[1]).getAllByRole('cell').map(cell=>cell.textContent)).toEqual(['8 次','4.3 秒','4,500'])
  expect(within(rows[2]).getAllByRole('cell').map(cell=>cell.textContent)).toEqual(['3 次调用','6.2 秒','1,000'])
  expect(within(rows[3]).getAllByRole('cell').map(cell=>cell.textContent)).toEqual(['2 次','1.5 秒','250'])
})

it('reads historic stage totals and preserves zero requests', () => {
  render(<DiagnosisUsage report={{...report,stage_metrics:[
    {id:'one',name:'第一阶段',seconds:1.2,requests:0,prompt_tokens:0,completion_tokens:0,cache_hits:1},
    {id:'two',name:'第二阶段',seconds:2.3,requests:0,prompt_tokens:0,completion_tokens:0,cache_hits:1},
  ]}}/>)
  const row = screen.getByRole('row',{name:/TrajDebug/})
  expect(within(row).getAllByRole('cell').map(cell=>cell.textContent)).toEqual(['0 次','3.5 秒','0'])
  expect(screen.queryByText('第一阶段')).toBeNull()
})
