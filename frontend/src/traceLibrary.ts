import type {DiagnosisMode, Job, Trace, TraceSummary} from './types'

export type LibrarySelection = Pick<TraceSummary, 'id' | 'title' | 'outcome' | 'input_format'> & {diagnosis?:string}
export type LibraryRow = LibrarySelection & Pick<TraceSummary, 'created_at' | 'dataset' | 'source' | 'steps'> & {
  diagnosis: string
  task_count: number
  has_result: boolean
  critical_step: number | null
  agent_name?: string | null
}
export interface LibraryPage {
  items: LibraryRow[]
  total: number
  page: number
  pages: number
  page_size: number
}
export interface LibraryDiagnosis {
  job_id:string; task_id:string|null; run_id:string|null; task_name:string|null
  status:string; stage:string; progress:number; error:string|null; created_at:string
  model:string|null; diagnosis_mode:DiagnosisMode|null
}
export interface LibraryHistory {
  tasks:{id:string; name:string; kind?:string}[]
  diagnoses:LibraryDiagnosis[]
}
export interface LibraryTraceDetail {
  trace:Trace; job:Job|null; latest:LibraryDiagnosis|null; selected:LibraryDiagnosis|null
  current_job_id:string|null; task_count:number
}

export const libraryLimit = 500
export const libraryDiagnosis:Record<string,string> = {
  pending:'待分析', queued:'排队中', running:'分析中', completed:'已分析', failed:'分析失败',
}
export const isAnalyzing = (status?:string) => status === 'queued' || status === 'running'
export const businessOutcomes = {failed: '业务失败', succeeded: '业务成功', unknown: '结果未知'}

export function libraryFilterQuery(params: URLSearchParams) {
  const query = new URLSearchParams()
  for (const key of ['search', 'diagnosis', 'business', 'input_format']) {
    const value = params.get(key)
    if (value && (key === 'search' || value !== 'all')) query.set(key, value)
  }
  return query.toString()
}

export function libraryPath(params: URLSearchParams, traceId?: string) {
  const path = traceId ? `/library/${traceId}` : '/library'
  return params.size ? `${path}?${params}` : path
}

export function libraryResultPath(traceId:string, params:URLSearchParams, jobId?:string) {
  const query = new URLSearchParams(params)
  if (jobId) query.set('analysis', jobId)
  else query.delete('analysis')
  return libraryPath(query, traceId)
}

export function openLibraryTask(params: URLSearchParams, selection: LibrarySelection[]) {
  const query = new URLSearchParams(libraryFilterQuery(params))
  for (const key of ['page', 'page_size']) {
    const value = params.get(key)
    if (value) query.set(key, value)
  }
  query.set('from', 'library')
  const librarySelection = selection.map(({id, title, outcome, input_format, diagnosis}) => ({id, title, outcome, input_format, diagnosis}))
  history.pushState({librarySelection}, '', `#/tasks/new?${query}`)
  window.dispatchEvent(new Event('hashchange'))
}
