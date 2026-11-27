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
  fireEvent.click(screen.getByRole('tab',{name:'TrajDebug'}))
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

it('regenerates diagnostics or feedback according to the active tab', () => {
  const regenerate = vi.fn(async () => {})
  const regenerateFeedback = vi.fn(async () => {})
  const withFeedback: Job = {...job,feedback_eligible:true,feedback:{id:'suggestion',job_id:job.id,
    status:'completed',error:null,created_at:job.created_at,updated_at:job.updated_at}}
  const props = {report,onJump:vi.fn(),onReview:vi.fn(),onRegenerate:regenerate,onRegenerateFeedback:regenerateFeedback}
  const {rerender} = render(<Inspector job={withFeedback} {...props}/>)
  fireEvent.click(screen.getByRole('tab',{name:'TrajDebug'}))
  for (const tab of [/诊断结论/, /错误实例/, /触发证据/]) {
    fireEvent.click(screen.getByRole('tab',{name:tab}))
    fireEvent.click(screen.getByRole('button',{name:'重新生成本条轨迹诊断'}))
  }
  expect(regenerate).toHaveBeenCalledTimes(3)
  fireEvent.click(screen.getByRole('tab',{name:'改进建议'}))
  fireEvent.click(screen.getByRole('button',{name:'重新生成改进建议'}))
  expect(regenerateFeedback).toHaveBeenCalledTimes(1)
  rerender(<Inspector job={{...withFeedback,feedback:{...withFeedback.feedback!,status:'running'}}} {...props}/>)
  expect((screen.getByRole('button',{name:'重新生成改进建议'}) as HTMLButtonElement).disabled).toBe(true)
  rerender(<Inspector job={{...withFeedback,read_only:true}} {...props}/>)
  expect(screen.queryByRole('button',{name:'重新生成改进建议'})).toBeNull()
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

it('opens dual synthesis first and keeps engine-specific inspection tabs', () => {
  const ad: Report = {...report, algorithm:'AgentDebugX', triggers:[], instances:[], evidence:[{step:4,quote:'原文'}],
    attribution:{...report.attribution!, critical_step:4, summary:'AgentDebugX 归因',fix_suggestions:['验证授权']}}
  const dual: Report = {...report, comparison:{state:'different',primary:'trajdebug'},
    engine_runs:{trajdebug:{status:'completed'}, agentdebugx:{status:'completed',report:ad}},
    synthesis:{status:'completed', summary:'两路对授权步骤存在分歧',preferred_engine:'agentdebugx',critical_step:4,
      assessments:{trajdebug:{support:'medium',reason:'支持部分结论'},agentdebugx:{support:'high',reason:'有原文'}},
      evidence:[{engine:'agentdebugx',step:4,quote:'综合分析引文'}],model:'fixture',calls:1,prompt_tokens:1,completion_tokens:1}}
  const jump = vi.fn()
  const generate = vi.fn(async () => {})
  render(<Inspector job={{...job,result:dual,feedback_eligible:true}} report={dual} onJump={jump} onReview={vi.fn()} onFeedback={generate}/>)
  const mainTabs = within(screen.getByRole('tablist',{name:'轨迹分析视图'}))
  expect(mainTabs.getAllByRole('tab').map(item=>item.textContent)).toEqual(['综合分析','改进建议'])
  expect(screen.getByRole('tab',{name:'综合分析'}).getAttribute('aria-selected')).toBe('true')
  expect(screen.getByText('两路对授权步骤存在分歧')).toBeTruthy()
  expect(screen.getByRole('heading',{name:'TrajDebug'})).toBeTruthy()
  expect(screen.getByRole('heading',{name:'AgentDebugX'})).toBeTruthy()
  const agentCard = screen.getByRole('heading',{name:'AgentDebugX'}).closest('section')!
  expect(within(agentCard).getByText('候选置信度')).toBeTruthy()
  expect(within(agentCard).getByText('高')).toBeTruthy()
  expect(screen.queryByText(/综合分析引文/)).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'关键步骤 #7'}))
  expect(jump).toHaveBeenLastCalledWith({step:7,quote:''})
  fireEvent.click(screen.getByRole('button',{name:/暂定关键步骤 #4.*定位轨迹/}))
  expect(jump).toHaveBeenLastCalledWith({step:4,quote:''})
  fireEvent.click(screen.getByRole('tab',{name:'TrajDebug'}))
  expect(within(screen.getByRole('tablist',{name:'引擎诊断内容'})).queryByRole('tab',{name:'改进建议'})).toBeNull()
  fireEvent.click(screen.getByRole('tab',{name:/错误实例/}))
  expect(screen.getByText('退款授权')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab',{name:'结论总览'}))
  fireEvent.click(screen.getByRole('button',{name:'查看AgentDebugX详情'}))
  expect(screen.queryByRole('tab',{name:/错误实例/})).toBeNull()
  fireEvent.click(screen.getByRole('tab',{name:'原文证据'}))
  expect(screen.getByText('“原文”')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab',{name:'恢复推演'}))
  expect(screen.getByText('验证授权')).toBeTruthy()
  fireEvent.click(mainTabs.getByRole('tab',{name:'改进建议'}))
  expect(screen.queryByRole('tab',{name:'AgentDebugX'})).toBeNull()
  expect(screen.queryByRole('tablist',{name:'引擎诊断内容'})).toBeNull()
  expect(generate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'生成改进建议'}))
  expect(generate).toHaveBeenCalledTimes(1)
  fireEvent.click(mainTabs.getByRole('tab',{name:'综合分析'}))
  expect(screen.getByRole('tab',{name:'AgentDebugX'}).getAttribute('aria-selected')).toBe('true')
})

it('shows only the AgentDebugX and CHIEF views for a Standard run', () => {
  const ad: Report = {...report, algorithm:'AgentDebugX', triggers:[], instances:[]}
  const chief: Report = {...report, algorithm:'CHIEF', triggers:[], instances:[],
    attribution:{...report.attribution!, summary:'CHIEF 归因'}}
  const standard: Report = {...ad, comparison:{state:'aligned',primary:'agentdebugx'},
    engine_runs:{agentdebugx:{status:'completed'},chief:{status:'completed',report:chief}}}
  render(<Inspector job={{...job,result:standard}} report={standard} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByRole('tab',{name:'综合分析'}).getAttribute('aria-selected')).toBe('true')
  expect(screen.getByRole('tab',{name:'AgentDebugX'})).toBeTruthy()
  expect(screen.queryByRole('tab',{name:'TrajDebug'})).toBeNull()
  fireEvent.click(screen.getByRole('tab',{name:'CHIEF'}))
  expect(screen.getByText('CHIEF 归因')).toBeTruthy()
})

it('shows the waiver and keeps manual review available for aligned results', () => {
  const dual: Report = {...report, comparison:{state:'aligned',primary:'trajdebug'},
    engine_runs:{trajdebug:{status:'completed'},agentdebugx:{status:'completed'}}}
  const review = vi.fn()
  render(<Inspector job={{...job,result:dual,review_status:'waived'}} report={dual} onJump={vi.fn()} onReview={review}/>)
  expect(screen.getByText('免复核')).toBeTruthy()
  expect(screen.getByRole('button',{name:/一致关键步骤 #7.*定位轨迹/})).toBeTruthy()
  expect(screen.queryByText(/候选置信度/)).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'仍要人工复核'}))
  expect(review).toHaveBeenCalledTimes(1)
})

it.each(['completed', 'failed'] as const)('shows the clickable agreed step with missing quotes and %s synthesis', status => {
  const dual: Report = {...report, comparison:{state:'same_step',primary:'trajdebug'},
    engine_runs:{trajdebug:{status:'completed'},agentdebugx:{status:'completed'},chief:{status:'completed'}},
    synthesis:status === 'completed' ? {status, summary:'引擎选中同一步骤', critical_step:null, preferred_engine:null,
      evidence:[], model:'fixture', calls:1} : {status, failure:'format'}}
  const jump = vi.fn()
  render(<Inspector job={{...job,result:dual,review_status:'waived'}} report={dual} onJump={jump} onReview={vi.fn()}/>)
  expect(screen.getByText('免复核')).toBeTruthy()
  expect(screen.getByText(/部分引擎缺少可核验的原文引用/)).toBeTruthy()
  expect(screen.getByText('历史复核参考')).toBeTruthy()
  expect(screen.queryByText('待人工复核')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:/一致关键步骤 #7.*定位轨迹/}))
  expect(jump).toHaveBeenCalledWith({step:7,quote:''})
  fireEvent.click(screen.getByRole('tab',{name:'TrajDebug'}))
  expect(screen.getByText('免复核')).toBeTruthy()
})

it('shows all three participants and can adopt and inspect the CHIEF candidate', () => {
  const source = {step:3, quote:'refund completed', matched:true, original_matched:true}
  const chief: Report = {...report, algorithm:'CHIEF', triggers:[], instances:[], evidence:[source],
    root_cause_agent:'billing', attribution:{...report.attribution!, critical_step:3, summary:'退款流程缺少审批'},
    official_output:{dag_graph:{subtasks:[{id:'S1', name:'提交退款', step_range:'1-3', oracle:'审批已完成',
      agents:[{agent:'billing'}], data_flow:[]}], subtask_edges:[], agent_edges:[]},
      candidates:{candidate_error_steps:[{step_id:3, agent_in_step:['billing'], irrecoverability_issue:{reason:'不可撤销'},
        data_issue:{explanation:''}}]}}}
  const combined: Report = {...report, comparison:{state:'different',primary:'trajdebug',engines:['trajdebug','agentdebugx','chief']},
    engine_runs:{trajdebug:{status:'completed'}, agentdebugx:{status:'completed'}, chief:{status:'completed',report:chief}},
    synthesis:{status:'completed',summary:'三路对退款根因给出不同步骤。',preferred_engine:'chief',critical_step:3,
      assessments:{trajdebug:{support:'medium',reason:'部分支持'},agentdebugx:{support:'low',reason:'证据较弱'},
        chief:{support:'high',reason:'有原文'}},evidence:[{...source,engine:'chief'}],model:'fixture',calls:1}}
  const jump = vi.fn()
  render(<Inspector job={{...job,result:combined}} report={combined} onJump={jump} onReview={vi.fn()}/>)
  expect(screen.getByText('纳入综合评估 3 路')).toBeTruthy()
  expect(screen.queryByText('历史复核参考')).toBeNull()
  const chiefCard = screen.getByRole('heading',{name:'CHIEF'}).closest('section')!
  expect(within(chiefCard).getByText('高')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:/暂定关键步骤 #3.*定位轨迹/}))
  expect(jump).toHaveBeenLastCalledWith({step:3,quote:''})
  fireEvent.click(screen.getByRole('button',{name:'查看CHIEF详情'}))
  expect(screen.getByText('根因 Agent · billing')).toBeTruthy()
  expect(within(screen.getByRole('tablist',{name:'引擎诊断内容'})).queryByRole('tab',{name:'改进建议'})).toBeNull()
  for (const tab of ['诊断结论', '证据与候选', '因果回溯']) {
    fireEvent.click(screen.getByRole('tab',{name:tab}))
    expect(screen.getByText('关键失败步骤')).toBeTruthy()
    jump.mockClear()
    fireEvent.click(screen.getByRole('button',{name:'#3'}))
    expect(jump).toHaveBeenCalledExactlyOnceWith({step:3,quote:''})
  }
  fireEvent.click(screen.getByRole('tab',{name:'证据与候选'}))
  fireEvent.click(screen.getByRole('button',{name:'步骤 #3'}))
  expect(jump).toHaveBeenLastCalledWith({step:3,quote:''})
  fireEvent.click(screen.getByRole('button',{name:/定位原文/}))
  expect(jump).toHaveBeenLastCalledWith(source)
  expect(screen.getByRole('link',{name:'下载 CHIEF 原始报告'}).getAttribute('href')).toBe('/api/jobs/job/artifacts/chief')
  fireEvent.click(screen.getByRole('tab',{name:'因果回溯'}))
  expect(screen.getByText('S1 · 提交退款 · 步骤 1-3')).toBeTruthy()
})

it('distinguishes a missing CHIEF key step from a successful trace', () => {
  const chief: Report = {...report,algorithm:'CHIEF',task_outcome:'failed',triggers:[],instances:[],
    attribution:{...report.attribution!,critical_step:null,critical_instance_id:null}}
  const jump = vi.fn()
  const {rerender} = render(<Inspector job={{...job,result:chief}} report={chief} onJump={jump} onReview={vi.fn()}/>)
  fireEvent.click(screen.getByRole('tab',{name:'CHIEF'}))
  fireEvent.click(screen.getByRole('tab',{name:'证据与候选'}))
  const control = screen.getByRole<HTMLButtonElement>('button',{name:'未确定'})
  expect(control.disabled).toBe(true)
  fireEvent.click(control)
  expect(jump).not.toHaveBeenCalled()

  const succeeded: Report = {...chief,task_outcome:'succeeded'}
  rerender(<Inspector job={{...job,result:succeeded}} report={succeeded} onJump={jump} onReview={vi.fn()}/>)
  expect(screen.queryByText('关键失败步骤')).toBeNull()
  expect(screen.queryByRole('tab',{name:'证据与候选'})).toBeNull()
})

it('links the overview to the reviewed step and clears the link on rejection', () => {
  const combined: Report = {...report, comparison:{state:'different',primary:'trajdebug'},
    engine_runs:{trajdebug:{status:'completed'},chief:{status:'completed',report:{...report,algorithm:'CHIEF'}}},
    synthesis:{status:'completed',summary:'模型综合结论',critical_step:7,preferred_engine:'trajdebug',
      evidence:[],model:'fixture',calls:1}}
  const jump = vi.fn()
  const review = {id:'review',created_at:job.created_at,
    data:{decision:'modified',critical_step:4,category:'',summary:'人工选择步骤四',notes:''}}
  const props = {report:combined,onJump:jump,onReview:vi.fn()}
  const reviewedJob = {...job,result:combined,reviews:[review]}
  const {rerender} = render(<Inspector job={reviewedJob} {...props}/>)
  fireEvent.click(screen.getByRole('button',{name:/人工复核关键步骤 #4/}))
  expect(jump).toHaveBeenCalledWith({step:4,quote:''})
  expect(screen.getByText('人工选择步骤四')).toBeTruthy()
  expect(screen.getAllByRole('button',{name:'关键步骤 #7'})).toHaveLength(2)
  rerender(<Inspector job={{...reviewedJob,reviews:[{...review,data:{...review.data,decision:'rejected',critical_step:null}}]}} {...props}/>)
  expect(screen.queryByRole('button',{name:/定位轨迹/})).toBeNull()
  expect(screen.getByText('已驳回')).toBeTruthy()
})

it('shows partial multi-engine synthesis and requires review', () => {
  const combined: Report = {...report, comparison:{state:'incomplete',primary:'trajdebug',engines:['trajdebug','agentdebugx']},
    engine_runs:{trajdebug:{status:'completed'},agentdebugx:{status:'completed'},chief:{status:'failed',error:'模型超时'}},
    synthesis:{status:'completed',summary:'已评估完成的两路。',preferred_engine:'trajdebug',critical_step:7,
      assessments:{trajdebug:{support:'high',reason:'原文支持'}},evidence:[],model:'fixture',calls:1}}
  render(<Inspector job={{...job,result:combined}} report={combined} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByText('纳入综合评估 2 路，部分引擎未完成。')).toBeTruthy()
  expect(screen.getByText('待人工复核')).toBeTruthy()
  expect(screen.queryByText('免复核')).toBeNull()
  expect(screen.getByText('模型超时')).toBeTruthy()
})
