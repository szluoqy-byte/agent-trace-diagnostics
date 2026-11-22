// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Job, Report } from '../types'
import Inspector from './Inspector'
import { ReviewDialog } from './Dialogs'

afterEach(cleanup)
const report: Report = {schema_version:2,algorithm:'TrajDebug',engine_version:'trajdebug-official-test',mode:'live',model:'fixture',
  triggers:[{id:'E001',step:4,category:'cat-1',taxonomy_tag:'act.UnsafeOrForbiddenAction',attribution:'agent',description:'无授权退款',confidence:'high',commitment:{step:null,quote:'unmatched approval',matched:false},reference:{step:1,quote:'不要退款',matched:true}}],
  instances:[{id:'I1',instance_id:1,object:'退款授权',attribution:'agent',trigger_ids:['E001'],origin_step:4,qualified_origin_step:7,last_trigger_step:7,origin_relocated:true,error_state:'manifest_active',terminal_relevant:true,terminal_connection:'irreversible',explanation:'错误持续影响最终结果',lifecycle_evidence:[]}],
  attribution:{critical_instance_id:'I1',critical_step:7,category:'act.UnsafeOrForbiddenAction',summary:'未经授权提交退款',reasoning:'原始模型解释',fix_suggestions:[],confidence:'unrated'}}
const job: Job = {id:'job',trace_id:'trace',mode:'live',status:'completed',stage:'诊断完成',progress:100,attempts:1,error:null,created_at:'2026-11-16',updated_at:'2026-11-16',result:report,reviews:[]}

it('shows relocated origins and only jumps from verified quotes', () => {
  const jump=vi.fn()
  render(<Inspector job={job} report={report} onJump={jump} onReview={vi.fn()}/>)
  fireEvent.click(screen.getByRole('tab',{name:/错误实例/}))
  expect(screen.getByText('初始信号 #4 → 有效错误承诺 #7')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'起点 #7'}))
  expect(jump).toHaveBeenLastCalledWith({step:7,quote:''})
  fireEvent.click(screen.getByRole('tab',{name:/触发证据/}))
  expect(screen.getByText('来源未定位 · 请核对诊断结果')).toBeTruthy()
  expect(screen.getAllByRole('button',{name:/定位原文/})).toHaveLength(1)
  fireEvent.click(screen.getByRole('button',{name:/定位原文/}))
  expect(jump).toHaveBeenLastCalledWith(report.triggers[0].reference)
  expect(screen.queryByRole('tab',{name:/约束/})).toBeNull()
})

it('keeps legacy reports separate from current native diagnosis', () => {
  render(<Inspector job={job} report={{...report,schema_version:undefined,engine_version:'fusion-0.5.1'}} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByText('历史版本诊断')).toBeTruthy()
  expect(screen.queryByText('未经授权提交退款')).toBeNull()
  expect(screen.queryByRole('button',{name:'人工复核并记录验证计划'})).toBeNull()
})

it('shows the three attribution fields on the conclusion and preserves unknown values', () => {
  const {rerender} = render(<Inspector job={job} report={report} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByText('责任归属')).toBeTruthy()
  expect(screen.getByText('Agent')).toBeTruthy()
  expect(screen.getByText('根因类别')).toBeTruthy()
  expect(screen.getByText('act.UnsafeOrForbiddenAction')).toBeTruthy()
  expect(screen.getByText('错误对象')).toBeTruthy()
  expect(screen.getByText('退款授权')).toBeTruthy()

  const unlinked = {...report,attribution:{...report.attribution!,critical_instance_id:null,category:'未提供分类'}}
  rerender(<Inspector job={job} report={unlinked} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByText('归属未确定')).toBeTruthy()
  expect(screen.getByText('未提供分类')).toBeTruthy()
  expect(screen.getByText('未关联错误实例')).toBeTruthy()
  expect(screen.queryByText('退款授权')).toBeNull()
})

it('keeps the original instance facts with the model conclusion when review changes the attribution', () => {
  const reviewed: Job = {...job,reviews:[{id:'review',created_at:job.created_at,data:{decision:'modified',
    critical_step:8,category:'reason.WrongChoice',summary:'人工结论',notes:''}}]}
  const {container} = render(<Inspector job={reviewed} report={report} onJump={vi.fn()} onReview={vi.fn()}/>)
  const [current,original] = container.querySelectorAll('dl.attribution-facts')
  expect(within(current as HTMLElement).getByText('reason.WrongChoice')).toBeTruthy()
  expect(within(current as HTMLElement).queryByText('退款授权')).toBeNull()
  expect(within(original as HTMLElement).getByText('退款授权')).toBeTruthy()
})

it('confirming after editing restores original fields and rejecting clears the step', () => {
  render(<ReviewDialog job={job} onClose={vi.fn()} onDone={vi.fn()}/>)
  const summary=screen.getByRole('textbox',{name:'诊断结论'}) as HTMLTextAreaElement
  expect(summary.readOnly).toBe(true)
  fireEvent.change(screen.getByRole('combobox',{name:'复核决定'}),{target:{value:'modified'}})
  fireEvent.change(summary,{target:{value:'edited conclusion'}})
  fireEvent.change(screen.getByRole('combobox',{name:'复核决定'}),{target:{value:'confirmed'}})
  expect(summary.value).toBe(report.attribution!.summary)
  fireEvent.change(screen.getByRole('combobox',{name:'复核决定'}),{target:{value:'rejected'}})
  expect((screen.getByRole('spinbutton',{name:'关键步骤（可留空）'}) as HTMLInputElement).value).toBe('')
})
