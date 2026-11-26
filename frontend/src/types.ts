export type EngineName = 'trajdebug' | 'agentdebugx' | 'chief'
export type DiagnosisLevel = 'fast' | 'standard' | 'deep'
export type DiagnosisMode = DiagnosisLevel | EngineName | 'dual' | 'dual_chief'
export type EvidenceSupport = 'high' | 'medium' | 'low' | 'insufficient'
export interface ModelUsage {
  requests: number
  prompt_tokens?: number
  completion_tokens?: number
  cache_hits?: number
  request_unit?: 'completion_calls'
}
export type DiagnosisSynthesis = {
  duration_seconds?: number; requests?: number; prompt_tokens?: number; completion_tokens?: number
} & (
  | {status: 'running' | 'incomplete' | 'not_needed' | 'insufficient'}
  | {status: 'failed'; failure?: 'http' | 'connection' | 'format' | 'empty' | 'length' | 'filtered'; http_status?: number; model?: string}
  | {status: 'completed'; summary: string; preferred_engine: EngineName | null; critical_step: number | null;
      assessments?: Partial<Record<EngineName, {support: EvidenceSupport; reason: string}>>;
      evidence: (Evidence & {engine: EngineName | 'trajectory'})[]; model: string; calls: number}
)
export interface EngineRun {
  status: 'running' | 'completed' | 'failed'
  error?: string
  report?: Report
  seconds?: number
  usage?: ModelUsage
}
export interface EngineComparison {
  state: 'aligned' | 'same_step' | 'different' | 'unresolved' | 'incomplete' | 'succeeded'
  primary: EngineName
  engines?: EngineName[]
}
interface EngineFinding { step_index?: number | null; failure_mode?: {mode_id?: string}; evidence?: string[] }
export interface Config { ready: boolean; model: string; base_url: string; has_key: boolean; concurrency: number; max_retries: number; timeout: number; llm_concurrency: number; max_tokens: number; cluster_json_mode: boolean; engine: string; algorithm?: string; upstream_commit?: string; upstream_url?: string }
export interface TraceSummary { id: string; title: string; created_at: string; dataset: string; task_id: string; outcome: string; kind: 'demo' | 'imported'; source: string; steps: number; input_format?: string }
export interface OTelSource { trace_id: string; span_id: string; attribute: string; message_index: number | null }
export interface OTelSpan { span: {spanId:string; [key:string]:unknown}; resource: Record<string,unknown>; scope: Record<string,unknown>; location: string }
export interface OTelProvenance { trace_id: string; adapter_version: string; spans: OTelSpan[]; warnings: string[] }
export interface Message { step: number; role: string; content: string; name: string; source_step: string; judgable?: boolean | null; compress_role?: string; source_refs?: OTelSource[] }
export interface Trace { id: string; data: { title: string; task_description: string; messages: Message[]; annotation: Record<string, unknown>; source: string; kind: string; outcome: string; otel?: OTelProvenance | null; input_format?: 'deepagent' | null; deepagent?: {trace_id: string; span_count: number} | null } }
export interface ViewMatch { step: number; tier: string; start: number; end: number }
export type CompressionViews = Record<string, Record<string, string>>
export interface Evidence { step?: number | null; source_id?: string | null; quote: string; start?: number; end?: number; matched?: boolean; original_matched?: boolean; source_kind?: 'original' | 'compressed' | 'task' | 'runtime' | 'framework' | 'unlocated'; view_matches?: ViewMatch[]; view_scope?: string }
export interface Trigger { id: string; step: number; category: string; taxonomy_tag: string; attribution: string; description: string; confidence: string; commitment: Evidence; reference: Evidence }
export interface Instance { id: string; instance_id: number; trigger_ids: string[]; object: string; origin_step: number; qualified_origin_step?: number | null; last_trigger_step: number; origin_relocated?: boolean; fix_status?: string; terminal_connection?: string; error_state: string; terminal_relevant: boolean; explanation: string; lifecycle_evidence: Evidence[]; attribution?: string; parse_failed?: boolean }
export interface Attribution { critical_instance_id: string | null; critical_step: number | null; category: string; summary: string; reasoning: string; fix_suggestions: string[]; confidence: string; source?: string }
export interface StageMetric { id: string; name: string; seconds: number; requests: number; prompt_tokens: number; completion_tokens: number; cache_hits: number }
export interface Report { duration_seconds?: number; synthesis?: DiagnosisSynthesis; task_outcome?: string; engine_runs?: Partial<Record<EngineName, EngineRun>>; comparison?: EngineComparison; engine_detection?: {findings?: EngineFinding[]}; root_cause_agent?: string | null; evidence?: Evidence[]; schema_version?: number; algorithm?: string; engine_version: string; mode: string; model: string; triggers: Trigger[]; instances: Instance[]; attribution?: Attribution; warnings?: string[]; evidence_protocol?: string; evidence_summary?: {total:number; original:number; compressed:number; unlocated:number}; compression_views?: CompressionViews; provenance?: {commit: string; repository?: string; run_id?: string | null; implementation?: string}; official_output?: Record<string, unknown>; stage_metrics?: StageMetric[]; usage?: ModelUsage }
export interface ReviewData { decision: string; critical_step: number | null; category: string; summary: string; notes: string }
export interface Review { id: string; data: ReviewData; created_at: string }
export interface FeedbackResult { hint_sentence: string; narrative_summary: string; critical_step: number | null; alternatives: {step:number | null; taxonomy_tag:string; reason:string; hint_sentence:string}[]; model: string; seconds?: number; usage?: {requests:number; prompt_tokens:number; completion_tokens:number; cache_hits:number}; provenance: {commit:string; diagnosis_job_id:string; feedback_run_id:string}; official_report: Record<string,unknown> }
export interface FeedbackRun { id:string; job_id:string; status:'queued' | 'running' | 'completed' | 'failed'; error:string | null; result?:FeedbackResult | null; created_at:string; updated_at:string }
export interface Job { id: string; trace_id: string; mode: string; status: string; stage: string; progress: number; attempts: number; error: string | null; created_at: string; updated_at: string; engine_version?: string; result?: Report; reviews?: Review[]; review_status?: string | null; feedback?:FeedbackRun | null; feedback_eligible?:boolean; feedback_unavailable_reason?:string; read_only?:boolean }
