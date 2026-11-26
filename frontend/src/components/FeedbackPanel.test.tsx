// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import FeedbackPanel from './FeedbackPanel'
import Inspector from './Inspector'
import type {FeedbackResult, FeedbackRun, Job, Report} from '../types'

afterEach(cleanup)
const report:Report = {schema_version:2,algorithm:'TrajDebug',mode:'live',engine_version:'trajdebug-official-test',model:'fixture',triggers:[],instances:[],attribution:{critical_step:1,critical_instance_id:null,category:'cat-1',summary:'original conclusion',reasoning:'original reasoning',fix_suggestions:[],confidence:'unrated'}}
const job:Job = {id:'job',trace_id:'trace',mode:'live',status:'completed',stage:'诊断完成',progress:100,attempts:1,error:null,created_at:'2026-11-18',updated_at:'2026-11-18',result:report,feedback_eligible:true}
const feedback = {id:'feedback',job_id:'job',status:'completed',error:null,created_at:'2026-11-18',updated_at:'2026-11-18',result:{hint_sentence:'Verify approval before issuing a refund.',narrative_summary:'An unapproved refund caused the failure.',critical_step:1,alternatives:[{step:3,taxonomy_tag:'reason.InvalidInference',reason:'Unverified completion claim.',hint_sentence:'Check the tool result.'}],model:'fixture',provenance:{commit:'5b04f98b1995ec758d9aba70940d17bb7d2d169b',diagnosis_job_id:'job',feedback_run_id:'feedback'},official_report:{},usage:{requests:1,prompt_tokens:20,completion_tokens:10,cache_hits:0}}} satisfies FeedbackRun

it('submits feedback explicitly and explains the additional model call', async () => {
  const generate=vi.fn(async()=>{})
  render(<FeedbackPanel job={job} onGenerate={generate}/>)
  expect(screen.getByText('生成建议会额外调用模型。')).toBeTruthy()
  expect(generate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'生成改进建议'}))
  await waitFor(()=>expect(generate).toHaveBeenCalledTimes(1))
})

it('keeps queued feedback from being resubmitted', () => {
  render(<FeedbackPanel job={{...job,feedback:{...feedback,status:'queued',result:null}}} onGenerate={vi.fn()}/>)
  expect(screen.getByRole('status').textContent).toContain('改进建议已排队')
  expect(screen.queryByRole('button',{name:'生成改进建议'})).toBeNull()
})

it('copies a generated hint and exposes source, alternative hints and usage', async () => {
  const copy=vi.fn(async()=>{})
  Object.defineProperty(navigator,'clipboard',{value:{writeText:copy},configurable:true})
  render(<FeedbackPanel job={{...job,feedback}}/>)
  expect(screen.getByText(feedback.result.hint_sentence)).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'复制改进指令'}))
  await waitFor(()=>expect(copy).toHaveBeenCalledWith(feedback.result.hint_sentence))
  expect(screen.getByRole('status').textContent).toBe('已复制改进指令')
  expect(screen.getByRole('link',{name:'改进建议 JSON'}).getAttribute('href')).toBe('/api/jobs/job/feedback/export')
  expect(screen.getByText('建议生成：1 次请求')).toBeTruthy()
  expect(screen.getByText('Check the tool result.')).toBeTruthy()
})

it('offers failure retry without changing the original diagnosis', async () => {
  const generate=vi.fn(async()=>{})
  render(<FeedbackPanel job={{...job,feedback:{...feedback,status:'failed',error:'官方反馈未生成可用的改进指令',result:null}}} onGenerate={generate}/>)
  expect(screen.getByRole('alert').textContent).toContain('未生成可用')
  fireEvent.click(screen.getByRole('button',{name:'重试生成建议'}))
  await waitFor(()=>expect(generate).toHaveBeenCalledTimes(1))
  expect(job.result!.attribution!.summary).toBe('original conclusion')
})

it('disables generation for an ineligible diagnosis and shows the reason', () => {
  render(<FeedbackPanel job={{...job,feedback_eligible:false,feedback_unavailable_reason:'官方反馈模块仅为失败轨迹生成改进建议'}} onGenerate={vi.fn()}/>)
  expect((screen.getByRole('button',{name:'生成改进建议'}) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText('改进建议仅为失败轨迹生成改进建议')).toBeTruthy()
})

it('opens the new inspector tab while preserving the original conclusion', () => {
  render(<Inspector job={{...job,feedback}} report={report} onJump={vi.fn()} onReview={vi.fn()}/>)
  expect(screen.getByText('original conclusion')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab',{name:'改进建议'}))
  expect(screen.getByRole('heading',{name:'改进建议'})).toBeTruthy()
  expect(screen.getByText(feedback.result.hint_sentence)).toBeTruthy()
  fireEvent.click(screen.getByRole('tab',{name:'综合分析'}))
  expect(screen.getByText('original conclusion')).toBeTruthy()
})

it('shows the trace-level recommendations and links to the evidence from the adopted step', () => {
  const result: FeedbackResult = {scope:'trace', critical_step:2, basis:'provisional', review_id:null,
    evidence:{id:'E1',step:2,quote:'unauthorized',summary:'Missing approval'},
    suggestions:[{priority:'high',problem:'缺少审批',change:'提交前检查审批状态',verification:'回放无审批轨迹',evidence_ids:['E1']}],
    model:'fixture',provenance:{diagnosis_job_id:'job',feedback_run_id:'feedback'}}
  const jump = vi.fn()
  render(<FeedbackPanel job={{...job,feedback:{...feedback,result}}} onJump={jump}/>)
  expect(screen.getByText('暂定结论 · 待复核')).toBeTruthy()
  expect(screen.getByText('提交前检查审批状态')).toBeTruthy()
  expect(screen.getByText('回放无审批轨迹')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'关键步骤 #2'}))
  expect(jump).toHaveBeenCalledWith({step:2,quote:'unauthorized'})
})

it('distinguishes the original diagnosis from a modified human review', () => {
  render(<FeedbackPanel job={{...job,feedback,reviews:[{id:'review',created_at:'2026-11-18',data:{decision:'modified',critical_step:3,category:'reviewed',summary:'changed',notes:''}}]}}/>)
  expect(screen.getByText(/人工复核已修改诊断结论/)).toBeTruthy()
  expect(screen.getByText(/关键步骤 #1/)).toBeTruthy()
})
