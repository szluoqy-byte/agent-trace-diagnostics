import type {Evidence} from './types'

export interface PatternScope {
  task_id?: string
  run_id?: string
  trace_ids?: string[]
}
export interface SourceDiagnosis {
  job_id: string
  status: string
  model: string | null
  source_task_id: string
  source_task: string
  source_run_id: string
  source_run_number: number
}
export interface PatternMember extends Partial<SourceDiagnosis> {
  trace_id: string
  title: string
  state: string
  outcome: string
  review_id?: string | null
  engine_version?: string | null
  category?: string
  object?: string
  critical_step?: number | null
  evidence?: Evidence
  evidence_role?: 'commitment' | 'reference' | 'lifecycle'
  instance_source?: 'official' | 'unique_same_step' | null
  lifecycle?: string
  tool?: string | null
}
export interface PatternPreview {
  scope: PatternScope
  snapshot_hash: string
  counts: Record<string, number>
  items: PatternMember[]
  choices: Record<string, SourceDiagnosis[]>
  engine_versions: [string, string][]
}
export interface FailurePattern {
  id: string
  category: string
  object: string
  responsibility: string
  count: number
  denominator: number
  share: number
  examples: {title: string; evidence_kind: string}[]
}
export interface PatternReview {
  decision: 'confirmed' | 'suspect'
  notes: string
  created_at: string
}
export interface PatternRun {
  id: string
  number: number
  created_at: string
  scope: PatternScope
  algorithm_version: string
  mapping_version: string
  reviews: Record<string, PatternReview>
  result: {
    counts: Record<string, number>
    selected: number
    eligible: number
    business_failed: number
    business_known: number
    repeated: number
    patterns: FailurePattern[]
  }
}
export interface PatternTask {
  id: string
  name: string
  runs: {id: string; number: number; created_at: string}[]
}
export interface PatternMembers {
  items: PatternMember[]
  total: number
  page: number
  page_size: number
  pages: number
}

export const patternStates: Record<string, string> = {
  included: '可用于分组', pending: '尚未完成诊断', succeeded: '业务成功', unknown: '业务结果未知',
  unsupported: '历史报告缺少分组所需信息', diagnosis_failed: '诊断失败', rejected: '复核已驳回',
  review_needs_evidence: '人工已修订，待补证', unattributed: '未定位失败归因',
  missing_instance: '已定位关键步骤，缺少关联错误实例', unlocated: '证据未定位', demo: '历史合成轨迹',
}
export const responsibilityLabels: Record<string, string> = {agent: 'Agent', env: '环境', unknown: '归属未确定'}
export const patternTaskPath = (id: string, run?: string) => `#/tasks/patterns/${id}${run ? '?run='+run : ''}`

export function openPatternCreate(scope: PatternScope, back: string, taskId?: string) {
  const query = new URLSearchParams()
  if (scope.task_id) query.set('source', scope.task_id)
  if (scope.run_id) query.set('run', scope.run_id)
  if (taskId) query.set('rerun', taskId)
  history.pushState({patternScope: scope, patternBack: back}, '', `#/tasks/patterns/new?${query}`)
  window.dispatchEvent(new Event('hashchange'))
}
