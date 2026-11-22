import {useState} from 'react'
import {Copy, Download, Lightbulb, RotateCcw} from 'lucide-react'
import {uiText} from '../branding'
import type {Job} from '../types'

export default function FeedbackPanel({job, onGenerate}: {job:Job; onGenerate?:()=>Promise<void>}) {
  const [submitting, setSubmitting] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const run = job.feedback
  const data = run?.status === 'completed' ? run.result : null
  const active = run?.status === 'queued' || run?.status === 'running'
  const originalStep = data?.critical_step ?? job.result?.attribution?.critical_step
  const review = job.reviews?.[0]?.data
  async function generate() {
    if (!onGenerate) return
    setSubmitting(true)
    try {await onGenerate()} finally {setSubmitting(false)}
  }
  async function copy() {
    try {await navigator.clipboard.writeText(data!.hint_sentence); setCopyMessage('已复制改进指令')}
    catch {setCopyMessage('复制失败，请手动选中改进指令并复制')}
  }
  return <div className="feedback-panel"><div className="section-title"><h2>改进建议</h2><span className="muted">{data ? '已生成' : run?.status === 'failed' ? '生成失败' : active ? '生成中' : '按需生成'}</span></div>
    <p className="muted feedback-explanation">基于模型原始诊断{originalStep != null ? `的关键步骤 #${originalStep}` : ''}，生成可加入 Agent 系统提示词的改进指令。应用前请人工核对建议并验证效果。</p>
    {review && review.decision !== 'confirmed' ? <p className="diagnosis-note">人工复核已{review.decision === 'rejected' ? '驳回' : '修改'}诊断结论；建议仍依据模型原始诊断生成，请对照复核记录判断是否采用。</p> : null}
    {data ? <><div className="feedback-hint"><div><Lightbulb size={16}/><strong>建议加入的指令</strong></div><blockquote>{data.hint_sentence}</blockquote></div><div className="feedback-actions"><button className="secondary small" onClick={()=>void copy()}><Copy size={14}/>复制改进指令</button><a className="secondary small" href={`/api/jobs/${job.id}/feedback/export`} download><Download size={14}/>改进建议 JSON</a></div>{copyMessage ? <p role="status" className="muted">{copyMessage}</p> : null}
      {data.narrative_summary ? <details className="feedback-details"><summary>失败过程总结</summary><p>{data.narrative_summary}</p></details> : null}
      {data.alternatives.length ? <details className="feedback-details"><summary>替代根因与对应建议 · {data.alternatives.length} 个候选</summary>{data.alternatives.map((item,i)=><article key={i}><strong>{item.step != null ? `#${item.step}` : '步骤未提供'} · {item.taxonomy_tag}</strong><p>{item.reason}</p>{item.hint_sentence ? <blockquote>{item.hint_sentence}</blockquote> : null}</article>)}</details> : null}
      <p className="feedback-origin muted">生成模型：{data.model}</p>{data.usage ? <div className="run-usage"><span>建议生成：{data.usage.requests} 次请求</span><span>{((data.usage.prompt_tokens+data.usage.completion_tokens)/1000).toFixed(1)}k tokens</span>{data.usage.cache_hits ? <span>{data.usage.cache_hits} 次缓存命中</span> : null}</div> : null}
    </> : active ? <div className="feedback-pending" role="status"><Lightbulb size={24}/><strong>{run.status === 'queued' ? '改进建议已排队' : '正在生成改进建议'}</strong><p>建议生成后将自动显示在此处。</p></div> : <>
      {run?.error ? <p role="alert" className="error-inline">{uiText(run.error)}</p> : null}
      {job.feedback_eligible ? <p className="muted feedback-explanation">生成建议会额外调用模型。</p> : null}
      {!job.feedback_eligible ? <p className="diagnosis-note">{uiText(job.feedback_unavailable_reason) || '请完成有效的失败诊断后生成建议。'}</p> : null}
      <button className="primary" disabled={!job.feedback_eligible || !onGenerate || submitting} onClick={()=>void generate()}>{run?.status === 'failed' ? <RotateCcw size={15}/> : <Lightbulb size={15}/>} {submitting ? '正在提交…' : run?.status === 'failed' ? '重试生成建议' : '生成改进建议'}</button>
    </>}
  </div>
}
