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
export const businessOutcomes = {failed: '业务失败', succeeded: '业务成功', unknown: '结果未知'} as const
export type KnownOutcome = 'failed'|'succeeded'
export const libraryPeriods = [
  ['today','今天'],['week','本周'],['month','本月'],['last7','最近 7 天'],['all','全部时间'],['custom','自定义'],
] as const

export function libraryDateValue(date:Date) {
  return [String(date.getFullYear()).padStart(4,'0'), String(date.getMonth()+1).padStart(2,'0'),
    String(date.getDate()).padStart(2,'0')].join('-')
}

function addDays(day:Date, offset:number) {
  const next = new Date(day)
  next.setDate(next.getDate()+offset)
  next.setHours(0,0,0,0)
  return next
}

function customTimeRange(from:string, to:string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to) || from > to) return null
  const start = new Date(from + 'T00:00:00')
  const lastDay = new Date(to + 'T00:00:00')
  const end = addDays(lastDay,1)
  return libraryDateValue(start) === from && libraryDateValue(lastDay) === to && start.getFullYear() >= 1 && end.getFullYear() <= 9999
    ? {start,end,lastDay} : null
}

export const validLibraryDates = (from:string,to:string) => !!customTimeRange(from,to)

export function libraryTimeRange(params:URLSearchParams, now = new Date()) {
  const requested = params.get('period')
  const custom = requested === 'custom' ? customTimeRange(params.get('date_from') ?? '', params.get('date_to') ?? '') : null
  const period = libraryPeriods.find(([value]) => value === requested && (value !== 'custom' || custom))?.[0] ?? 'week'
  if (period === 'all') return {period, range:null}
  if (custom) return {period, range:custom}
  const today = new Date(now.getFullYear(),now.getMonth(),now.getDate())
  const monday = addDays(today, -((today.getDay()+6)%7))
  const start = period === 'today' ? today : period === 'last7' ? addDays(today,-6)
    : period === 'month' ? new Date(today.getFullYear(),today.getMonth(),1) : monday
  const end = period === 'week' ? addDays(monday,7)
    : period === 'month' ? new Date(today.getFullYear(),today.getMonth()+1,1) : addDays(today,1)
  return {period, range:{start,end,lastDay:addDays(end,-1)}}
}

export function libraryFilterQuery(params: URLSearchParams) {
  const query = new URLSearchParams()
  for (const key of ['search', 'diagnosis', 'business', 'input_format']) {
    const value = params.get(key)
    if (value && (key === 'search' || value !== 'all')) query.set(key, value)
  }
  const {period,range} = libraryTimeRange(params)
  if (period !== 'week') query.set('period',period)
  if (period === 'custom' && range) {
    query.set('date_from',libraryDateValue(range.start))
    query.set('date_to',libraryDateValue(range.lastDay))
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
