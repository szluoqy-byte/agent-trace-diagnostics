import { useState } from 'react'
import {uiText} from '../branding'
import Citation from './Citation'
import Tabs from './Tabs'
import FeedbackPanel from './FeedbackPanel'
import DiagnosisComparison, {AgentDebugXDetails, comparisonLabels} from './DiagnosisComparison'
import {responsibilityLabels} from '../patterns'
import { ArrowUpRight, CircleCheck, UserRoundCheck, ScanSearch, GitBranch, Flag, ChevronRight, RotateCcw } from 'lucide-react'
import type { Evidence, Instance, Job, Report } from '../types'

const states: Record<string, string> = {manifest_active:'持续影响最终结果', latent_active:'未修复 · 未影响最终结果', clean_resolution:'已修复 · 未影响最终结果', costly_resolution:'已修复 · 仍有资源代价', exploration:'探索行为 · 已排除', unknown:'状态待核验'}
const references: Record<string, string> = {'cat-1':'与任务要求冲突', 'cat-2':'与可见上下文冲突', 'cat-3':'内部不一致', env:'环境异常'}
const connections: Record<string,string> = {irreversible:'不可逆状态改变', semantic:'错误结论仍被沿用', semantic_uncertain:'与最终结果的关联待确认', budget_debt:'资源消耗影响最终结果', none:'未发现与最终结果相关'}

function InstanceCard({item, report, onJump}: {item:Instance; report:Report; onJump:(e:Evidence)=>void}) {
  const origin = item.qualified_origin_step ?? item.origin_step
  const fixed = item.fix_status?.startsWith('fixed_at_step_') ? Number(item.fix_status.replace('fixed_at_step_','')) : null
  const triggers = report.triggers.filter(t => item.trigger_ids.includes(t.id))
  return <article className={'instance-card ' + (item.terminal_relevant ? 'responsible' : '')}><div className="instance-top"><code>{item.id}</code><span className="mini-meta">{responsibilityLabels[item.attribution ?? ''] ?? responsibilityLabels.unknown}</span><span className={'state-pill ' + item.error_state}>{states[item.error_state] ?? item.error_state}</span></div><h3>{item.object}</h3><div className="episode-line"><button onClick={() => onJump({step:origin, quote:''})}>起点 #{origin}</button><i/><span>{fixed == null ? '持续至轨迹终点' : '修复 #' + fixed}</span></div>{item.origin_relocated ? <p className="relocation">初始信号 #{item.origin_step} → 有效错误承诺 #{origin}</p> : null}<p className="instance-explanation">{item.explanation || '本次结果未提供解释。'}</p><div className="instance-connection"><GitBranch size={14}/>{connections[item.terminal_connection ?? 'none'] ?? item.terminal_connection}</div><details><summary>展开触发与修复证据 <span>{triggers.length} 个触发点</span></summary><div className="instance-triggers">{triggers.map(t => <button className="secondary small" key={t.id} onClick={() => onJump({step:t.step, quote:(t.commitment.original_matched ?? (t.commitment.source_kind !== 'compressed' && t.commitment.matched)) ? t.commitment.quote : ''})}>{t.id} · #{t.step}<ChevronRight size={12}/></button>)}</div>{item.lifecycle_evidence.map((e,n) => <Citation key={n} evidence={e} views={report.compression_views} onJump={onJump}/>)}</details></article>
}

function AttributionFacts({category, instance}: {category?: string; instance?: Instance}) {
  const responsibility = responsibilityLabels[instance?.attribution ?? ''] ?? responsibilityLabels.unknown
  return <dl className="attribution-facts">
    <dt>责任归属</dt><dd>{responsibility}</dd>
    <dt>根因类别</dt><dd><span className="attribution-category">{category || '未提供分类'}</span></dd>
    <dt>错误对象</dt><dd>{instance?.object || '未关联错误实例'}</dd>
  </dl>
}

export default function Inspector({job, report, onJump, onReview, onFeedback, onRegenerate, onRegenerateFeedback, busy=false, draft=false}: {
  job:Job | null; report:Report | null; onJump:(e:Evidence)=>void; onReview:()=>void; onFeedback?:()=>Promise<void>;
  onRegenerate?:()=>Promise<void>; onRegenerateFeedback?:()=>Promise<void>; busy?:boolean; draft?:boolean
}) {
  const [tab, setTab] = useState('conclusion')
  const [instanceFilter, setInstanceFilter] = useState('all')
  const ai = report?.attribution
  const review = job?.reviews?.[0]
  const conclusion = review?.data ?? ai
  const completed = job?.status === 'completed'
  const legacy = completed && report?.schema_version !== 2
  const instances = report?.instances ?? []
  const modelInstance = ai?.critical_instance_id ? instances.find(item => item.id === ai.critical_instance_id) : undefined
  const reviewMatchesAttribution = !review || (review.data.decision !== 'rejected' &&
    review.data.critical_step === ai?.critical_step && review.data.category === ai?.category)
  const conclusionInstance = reviewMatchesAttribution ? modelInstance : undefined
  const agentDebugX = report?.algorithm === 'AgentDebugX'
  const showAttribution = report?.task_outcome !== 'succeeded' && report?.official_output?.task_success !== true
  const filtered = instanceFilter === 'responsible' ? instances.filter(i => i.terminal_relevant) : instances
  const canInspect = !!ai && completed && !legacy
  const tabs = [{id:'conclusion',label:'诊断结论'}]
  if (canInspect) {
    if (agentDebugX) tabs.push({id:'agentdebugx', label:'证据与建议'})
    else tabs.push({id:'instances',label:'错误实例'}, {id:'evidence',label:'触发证据'}, {id:'feedback',label:'改进建议'})
  }
  if (report?.engine_runs) tabs.push({id:'comparison',label:'双路对照'})
  const activeTab = tabs.some(item => item.id === tab) ? tab : 'conclusion'
  const items = tabs.map(item => ({...item, count:item.id === 'instances' ? instances.length
    : item.id === 'evidence' ? report?.triggers.length : undefined}))
  const feedbackTab = activeTab === 'feedback'
  const feedbackStatus = job?.feedback?.status
  const feedbackActive = feedbackStatus === 'queued' || feedbackStatus === 'running'
  const regenerate = feedbackTab ? (feedbackStatus === 'failed' ? onFeedback : onRegenerateFeedback) : onRegenerate
  const canShowRegenerate = !!regenerate && completed && !job?.read_only && (!feedbackTab || !!feedbackStatus)
  const regenerateLabel = feedbackTab ? (feedbackStatus === 'failed' ? '重试生成改进建议' : '重新生成改进建议') : '重新生成本条轨迹诊断'
  const regenerateTips = feedbackActive ? '请等待本条轨迹的改进建议生成完成'
    : feedbackTab ? job?.feedback_unavailable_reason || '使用当前诊断重新生成改进建议'
    : '重新分析本条轨迹并生成诊断结论、错误实例和触发证据'
  return <section className="panel inspector">
    <div className="inspector-toolbar">
      <Tabs id="inspection" label="诊断内容" className="inspector-tabs"
        items={items} active={activeTab} onChange={setTab}/>
      {canShowRegenerate ? <span className="inspector-regenerate" title={regenerateTips}>
        <button type="button" className="secondary small" aria-label={regenerateLabel}
          disabled={busy || feedbackActive || (feedbackTab && !job?.feedback_eligible)}
          onClick={() => void regenerate?.()}><RotateCcw size={15} aria-hidden="true"/></button>
      </span> : null}
    </div>
    <div className="inspector-scroll" role="tabpanel" id={`inspection-panel-${activeTab}`}
      aria-labelledby={`inspection-tab-${activeTab}`}>
    {activeTab === 'comparison' && report ? <DiagnosisComparison report={report} jobId={job?.id} onJump={onJump}/>
    : activeTab === 'agentdebugx' && report ? <AgentDebugXDetails report={report} jobId={job?.id} onJump={onJump}/>
    : legacy ? <div className="empty inspector-empty"><GitBranch size={36}/><h3>历史版本诊断</h3><p>该结果保留在历史记录中。重新诊断后，可在这里查看错误实例与生命周期。</p><p className="muted">原始报告仍可导出。</p></div> : !ai || !completed ? <div className="empty inspector-empty"><ScanSearch size={36}/><h3>{job?.status === 'failed' ? '诊断失败' : job?.status === 'queued' ? '等待诊断' : job?.status === 'running' ? '正在诊断' : '尚未诊断'}</h3><p>{uiText(job?.error) || (job?.status === 'failed' ? '可通过「重试诊断」重新提交。' : job ? '诊断完成后将在此显示结果。' : draft?'请返回任务详情启动分析。':'启动诊断后，可在此查看结论和引用证据。')}</p></div> : activeTab === 'feedback' && job ? <FeedbackPanel key={job.feedback?.id ?? job.id} job={job} onGenerate={onFeedback}/> : activeTab === 'instances' ? <><div className="section-title"><h2>错误生命周期</h2><span className="muted">按错误实例展示</span></div><div className="instance-filters"><button className={instanceFilter === 'all' ? 'active' : ''} aria-pressed={instanceFilter==='all'} onClick={() => setInstanceFilter('all')}>全部 {instances.length}</button><button className={instanceFilter === 'responsible' ? 'active' : ''} aria-pressed={instanceFilter==='responsible'} onClick={() => setInstanceFilter('responsible')}>影响最终结果 {instances.filter(i => i.terminal_relevant).length}</button></div>{filtered.map(item => <InstanceCard key={item.id} item={item} report={report!} onJump={onJump}/>)}{!filtered.length ? <p className="muted">没有符合当前筛选条件的错误实例。</p> : null}</> : activeTab === 'evidence' ? <><div className="section-title"><h2>逐步触发证据</h2><span className="muted">查看引用视图或对照原文</span></div>{report?.triggers.map(t => <article className="trigger-block" key={t.id}><div className="trigger-title"><code>{t.id}</code><span>{references[t.category] ?? t.category}</span><button className="text-button" onClick={() => onJump({step:t.step,quote:''})}>#{t.step}</button></div><div className="taxonomy">{t.taxonomy_tag}</div><p>{t.description}</p><label className="evidence-label">错误行为或环境异常</label><Citation evidence={t.commitment} views={report?.compression_views} onJump={onJump}/><label className="evidence-label">相关约束或参考依据</label><Citation evidence={t.reference} views={report?.compression_views} onJump={onJump}/></article>)}{!report?.triggers.length ? <p className="muted">本次诊断未检出错误触发点。</p> : null}</> : <>
      <div className="section-title"><h2>{showAttribution?'关键失败归因':'诊断结果'}</h2><span className={'status-tag ' + (review ? 'green' : '')}>{review ? review.data.decision === 'rejected' ? '已驳回' : '已复核' : '待人工复核'}</span></div>
      {report?.comparison ? <div className="diagnosis-note">
        {comparisonLabels[report.comparison.state]}。<button type="button" className="text-button" onClick={() => setTab('comparison')}>查看双路对照</button>
      </div> : null}
      {report?.mode === 'demo' ? <div className="diagnosis-note">此历史记录使用合成数据，未运行模型</div> : null}
      {showAttribution ? <div className="verdict-step"><Flag size={20}/><div><span>关键失败步骤</span><button className="text-button" disabled={conclusion?.critical_step == null} onClick={() => onJump({step:conclusion?.critical_step,quote:''})}>{conclusion?.critical_step == null ? '未确定' : '#' + conclusion.critical_step}<ArrowUpRight size={17}/></button></div></div> : null}
      {showAttribution && !agentDebugX ? <AttributionFacts category={conclusion?.category} instance={conclusionInstance}/> : null}
      <div className="conclusion"><strong>{conclusion?.summary}</strong></div>
      {conclusion?.critical_step != null && !conclusionInstance ? <div className="diagnosis-note">
        {reviewMatchesAttribution ? '可点击关键失败步骤核对原文。' : '可展开模型原始结论查看原归因。'}
      </div> : null}
      {report?.evidence_summary ? <div className="citation-summary">引用来源：原文 {report.evidence_summary.original} · 压缩视图 {report.evidence_summary.compressed} · 未定位 {report.evidence_summary.unlocated}<small>请结合原文核对引用与诊断结论的关联。</small></div> : null}
      {report?.warnings?.map((w,i) => <div className="diagnosis-note" key={i}>{uiText(w)}</div>)}
      {review ? <div className="review-note"><CircleCheck size={15}/><div>人工复核已保存{review.data.notes ? <p>{review.data.notes}</p> : null}<details><summary>查看模型原始结论</summary><p>{ai.summary} · #{ai.critical_step ?? '—'}</p>{showAttribution && !agentDebugX && !reviewMatchesAttribution ? <AttributionFacts category={ai.category} instance={modelInstance}/> : null}</details></div></div> : null}
      {!agentDebugX ? <div className="diagnosis-stats"><button onClick={() => setTab('instances')}><strong>{instances.length}</strong><span>错误实例</span></button><button onClick={() => {setInstanceFilter('responsible');setTab('instances')}}><strong>{instances.filter(i => i.terminal_relevant).length}</strong><span>影响最终结果</span></button><button onClick={() => setTab('evidence')}><strong>{report?.triggers.length ?? 0}</strong><span>触发证据</span></button></div> : null}
      {showAttribution && !agentDebugX ? <><h3 className="subheading">复核提示</h3><p className="muted">先核对影响最终结果实例的起点与修复证据，再确认或修改诊断结论。</p>
      {ai.fix_suggestions.length ? <ol className="suggestions">{ai.fix_suggestions.map((s,i) => <li key={i}>{s}</li>)}</ol> : <button className="secondary small" onClick={() => setTab('feedback')}>生成或查看改进建议</button>}</> : null}
      {report?.usage ? <div className="run-usage"><span>{report.usage.requests} 次{agentDebugX ? '模型调用' : '模型请求'}</span><span>{((report.usage.prompt_tokens + report.usage.completion_tokens) / 1000).toFixed(1)}k tokens</span>{report.usage.cache_hits ? <span>{report.usage.cache_hits} 次缓存命中</span> : null}</div> : null}
    </>}
    </div>{ai && completed && !legacy && !job?.read_only ? <div className="inspector-footer"><button className="primary full" onClick={onReview}><UserRoundCheck size={17}/>{review ? '再次复核' : '人工复核'}</button></div> : null}</section>
}
