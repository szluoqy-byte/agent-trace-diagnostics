import type {TraceSummary} from './types'
import {diagnosisStatus} from './workspace'

export type LibrarySelection = Pick<TraceSummary, 'id' | 'title' | 'outcome' | 'input_format'>
export type LibraryRow = LibrarySelection & Pick<TraceSummary, 'created_at' | 'dataset' | 'source' | 'steps'> & {
  diagnosis: string
  task_count: number
  agent_name?: string | null
}
export interface LibraryPage {
  items: LibraryRow[]
  total: number
  page: number
  pages: number
  page_size: number
}
export interface LibraryHistory {
  tasks: {id: string; name: string; agent: string; version: string; kind?: string}[]
  diagnoses: {job_id: string; task_id: string | null; run_id: string | null; number: number | null;
    status: string; created_at: string; model: string | null}[]
}

export const libraryLimit = 500
export const libraryDiagnosis = diagnosisStatus
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

export function openLibraryTask(params: URLSearchParams, selection: LibrarySelection[]) {
  const query = new URLSearchParams(params)
  query.set('from', 'library')
  const librarySelection = selection.map(({id, title, outcome, input_format}) => ({id, title, outcome, input_format}))
  history.pushState({librarySelection}, '', `#/tasks/new?${query}`)
  window.dispatchEvent(new Event('hashchange'))
}
