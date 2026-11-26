import {useState} from 'react'
import {ArrowUpRight, Copy, Download, Lightbulb, RotateCcw} from 'lucide-react'
import {uiText} from '../branding'
import type {Evidence, Job} from '../types'

const basisLabels = {review:'人工复核', synthesis:'综合结论', provisional:'暂定结论 · 待复核', diagnosis:'诊断结论'}
const priorityLabels = {high:'高优先级', medium:'中优先级', low:'低优先级'}

export default function FeedbackPanel({job, onGenerate, onJump}: {job:Job; onGenerate?:()=>Promise<void>; onJump?:(e:Evidence)=>void}) {
  const [submitting, setSubmitting] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const run = job.feedback
  const data = run?.status === 'completed' ? run.result : null
  const active = run?.status === 'queued' || run?.status === 'running'
  const current = data?.scope === 'trace' ? data : null
  const legacy = data?.scope !== 'trace' ? data : null
  const review = job.reviews?.[0]
  async function generate() {
    if (!onGenerate) return
    setSubmitting(true)
    try {await onGenerate()} finally {setSubmitting(false)}
  }
  async function copy() {
    if (!legacy) return
    try {await navigator.clipboard.writeText(legacy.hint_sentence); setCopyMessage('已复制改进指令')}
    catch {setCopyMessage('复制失败，请手动选中改进指令并复制')}
  }
  return <div className="feedback-panel"><div className="section-title"><h2>改进建议</h2><span className="muted">{data ? '已生成' : run?.status === 'failed' ? '生成失败' : active ? '生成中' : '按需生成'}</span></div>
    {!data ? <p className="muted feedback-explanation">结合本条轨迹的诊断结论和关键步骤原文，生成具体改动和回归验证建议。</p> : null}
    {current ? <>
      <div className="feedback-basis">
        <span>{basisLabels[current.basis]}</span>
        {onJump ? <button type="button" className="text-button"
          onClick={()=>onJump({step:current.critical_step, quote:current.evidence.quote})}>
          关键步骤 #{current.critical_step}<ArrowUpRight size={14} aria-hidden="true"/>
        </button> : <span>关键步骤 #{current.critical_step}</span>}
      </div>
      {(review?.id ?? null) !== current.review_id ? <p className="diagnosis-note">
        {review?.data.decision === 'rejected' ? '最新复核已驳回这份建议所依据的诊断结论。' : '复核结论已更新，当前建议仍依据之前的结论。'}
      </p> : null}
      {current.suggestions.length ? current.suggestions.map((item,i)=><article className="instance-card feedback-suggestion" key={i}>
        <div className="instance-top"><h3>{item.problem}</h3><span className="mini-meta">{priorityLabels[item.priority]}</span></div>
        <dl><dt>建议改动</dt><dd>{item.change}</dd><dt>回归验证</dt><dd>{item.verification}</dd></dl>
      </article>) : <p className="diagnosis-note">现有证据不足以提出具体改进建议。</p>}
    </> : legacy ? <>
      <p className="muted feedback-explanation">依据模型原始诊断{legacy.critical_step != null ? `的关键步骤 #${legacy.critical_step}` : ''}生成。应用前请人工核对并验证效果。</p>
      {review && review.data.decision !== 'confirmed' ? <p className="diagnosis-note">人工复核已{review.data.decision === 'rejected' ? '驳回' : '修改'}诊断结论。请对照复核记录判断是否采用建议。</p> : null}
      <div className="feedback-hint"><div><Lightbulb size={16}/><strong>建议加入的指令</strong></div><blockquote>{legacy.hint_sentence}</blockquote></div>
      {legacy.narrative_summary ? <details className="feedback-details"><summary>失败过程总结</summary><p>{legacy.narrative_summary}</p></details> : null}
      {legacy.alternatives.length ? <details className="feedback-details"><summary>替代根因与对应建议 · {legacy.alternatives.length} 个候选</summary>{legacy.alternatives.map((item,i)=><article key={i}><strong>{item.step != null ? `#${item.step}` : '步骤未提供'} · {item.taxonomy_tag}</strong><p>{item.reason}</p>{item.hint_sentence ? <blockquote>{item.hint_sentence}</blockquote> : null}</article>)}</details> : null}
    </> : null}
    {data ? <>
      <div className="feedback-actions">{legacy ? <button className="secondary small" onClick={()=>void copy()}><Copy size={14}/>复制改进指令</button> : null}<a className="secondary small" href={`/api/jobs/${job.id}/feedback/export`} download><Download size={14}/>改进建议 JSON</a></div>
      {copyMessage ? <p role="status" className="muted">{copyMessage}</p> : null}
      <p className="feedback-origin muted">生成模型：{data.model}</p>{data.usage ? <div className="run-usage"><span>建议生成：{data.usage.requests} 次请求</span><span>{((data.usage.prompt_tokens+data.usage.completion_tokens)/1000).toFixed(1)}k tokens</span>{data.usage.cache_hits ? <span>{data.usage.cache_hits} 次缓存命中</span> : null}</div> : null}
    </> : active ? <div className="feedback-pending" role="status"><Lightbulb size={24}/><strong>{run.status === 'queued' ? '改进建议已排队' : '正在生成改进建议'}</strong><p>建议生成后将自动显示在此处。</p></div> : <>
      {run?.error ? <p role="alert" className="error-inline">{uiText(run.error)}</p> : null}
      {job.feedback_eligible ? <p className="muted feedback-explanation">生成建议会额外调用模型。</p> : null}
      {!job.feedback_eligible ? <p className="diagnosis-note">{uiText(job.feedback_unavailable_reason) || '请完成有效的失败诊断后生成建议。'}</p> : null}
      <button className="primary" disabled={!job.feedback_eligible || !onGenerate || submitting} onClick={()=>void generate()}>{run?.status === 'failed' ? <RotateCcw size={15}/> : <Lightbulb size={15}/>} {submitting ? '正在提交…' : run?.status === 'failed' ? '重试生成建议' : '生成改进建议'}</button>
    </>}
  </div>
}
