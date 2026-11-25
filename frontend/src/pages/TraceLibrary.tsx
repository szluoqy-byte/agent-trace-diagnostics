import {useEffect, useRef, useState} from 'react'
import {Plus, RefreshCw, Search, Upload} from 'lucide-react'
import {api, dateLabel} from '../api'
import {sourceLabel} from '../branding'
import {openPatternCreate} from '../patterns'
import Pagination from '../components/Pagination'
import {businessOutcomes, libraryDiagnosis, libraryLimit, libraryPath, openLibraryTask} from '../traceLibrary'
import type {LibraryPage, LibrarySelection} from '../traceLibrary'
import {Badge, formatLabel, useResource, useWorkspace} from '../workspace'

const poll = (data: LibraryPage) => data.items.some(row => ['running', 'queued'].includes(row.diagnosis)) ? 2500 : 0

export default function TraceLibrary({params, selected, onSelect}: {
  params: URLSearchParams; selected: LibrarySelection[]; onSelect: (items: LibrarySelection[]) => void
}) {
  const {go, version, refresh, notify} = useWorkspace()
  const search = params.get('search') ?? ''
  const [searchInput, setSearchInput] = useState(search)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')
  const importInput = useRef<HTMLInputElement>(null)
  useEffect(() => setSearchInput(search), [search])

  const requestedPage = Number(params.get('page'))
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1
  const requestedSize = Number(params.get('page_size'))
  const size = [10, 20, 50].includes(requestedSize) ? requestedSize : 10
  const filters = {
    diagnosis: params.get('diagnosis') ?? '',
    business: params.get('business') ?? '',
    input_format: params.get('input_format') ?? '',
  }
  const query = new URLSearchParams({page: String(page), page_size: String(size)})
  if (search) query.set('search', search)
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value)
  const {data, loading, error} = useResource<LibraryPage>(`/trace-library?${query}`, version, poll)

  const ids = new Set(selected.map(row => row.id))
  const selectable = (data?.items ?? []).filter(row => row.outcome !== 'unknown')
  const allChecked = selectable.length > 0 && selectable.every(row => ids.has(row.id))
  const someChecked = selectable.some(row => ids.has(row.id))
  const unselected = selectable.filter(row => !ids.has(row.id))
  const atLimit = ids.size >= libraryLimit
  const hasFilters = !!(search || filters.diagnosis || filters.business || filters.input_format)

  function update(patch: Record<string, string>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    go(libraryPath(next))
  }

  function togglePage(checked: boolean) {
    const pageIds = new Set(selectable.map(row => row.id))
    onSelect(checked ? [...selected, ...unselected] : selected.filter(row => !pageIds.has(row.id)))
  }

  async function importFiles(files: File[]) {
    if (!files.length || importing) return
    setImporting(true)
    setImportError('')
    try {
      const form = new FormData()
      files.forEach(file => form.append('files', file))
      const result = await api<{trace_ids: string[]}>('/import', {method: 'POST', body: form})
      notify(`已导入 ${result.trace_ids.length} 条轨迹`)
      refresh()
    } catch (error) {
      setImportError((error as Error).message)
    } finally {
      setImporting(false)
    }
  }

  return <>
    <div className="ws-heading">
      <div><h1>轨迹库</h1><p>导入或查找轨迹，查看诊断记录或复用到新任务。支持导入 DeepAgent 和 OTel GenAI 轨迹文件。</p></div>
      <div className="ws-actions"><input ref={importInput} hidden type="file" multiple accept=".json,.jsonl,application/json,application/x-ndjson"
        aria-label="选择要导入的轨迹文件" onChange={event => {void importFiles(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
        <button className="secondary" disabled={importing} onClick={() => importInput.current?.click()}><Upload size={15}/>{importing ? '正在导入…' : '导入轨迹'}</button>
        <button className="secondary" onClick={refresh}><RefreshCw size={15}/>刷新</button></div>
    </div>
    {importError ? <p className="error-inline" role="alert">{importError}</p> : null}
    <form className="ws-filters library-filters" onSubmit={event => {
      event.preventDefault()
      update({search: searchInput.trim(), page: ''})
    }}>
      <input type="search" aria-label="搜索轨迹" placeholder="标题、轨迹 ID、来源文件或数据集" maxLength={200}
        value={searchInput} onChange={event => setSearchInput(event.target.value)}/>
      <button className="secondary" type="submit"><Search size={15}/>搜索</button>
      <select aria-label="诊断状态" value={filters.diagnosis} onChange={event => update({diagnosis: event.target.value, page: ''})}>
        <option value="">全部诊断状态</option>
        {Object.entries(libraryDiagnosis).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <select aria-label="业务结果" value={filters.business} onChange={event => update({business: event.target.value, page: ''})}>
        <option value="">全部业务结果</option>
        {Object.entries(businessOutcomes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <select aria-label="输入格式" value={filters.input_format} onChange={event => update({input_format: event.target.value, page: ''})}>
        <option value="">全部输入格式</option>
        {['messages', 'otel_genai', 'deepagent'].map(value => <option key={value} value={value}>{formatLabel(value)}</option>)}
      </select>
      {hasFilters ? <button type="button" className="text-button" onClick={() => {
        setSearchInput('')
        update({search: '', diagnosis: '', business: '', input_format: '', page: ''})
      }}>清除筛选</button> : null}
    </form>
    <div className="ws-selection">
      <strong aria-live="polite">已选 {selected.length} 条</strong>
      <span>{atLimit ? '已达到每任务 500 条上限' : '切换分页或筛选时保留选择'}</span>
      <div className="ws-actions">
        {selected.length ? <button className="text-button" onClick={() => onSelect([])}>清空选择</button> : null}
        <button className="primary small" disabled={!selected.length} onClick={() => openLibraryTask(params, selected)}>
          <Plus size={14}/>新建诊断任务
        </button>
        <button className="secondary small" disabled={selected.length<2} onClick={() =>
          openPatternCreate({trace_ids:selected.map(row=>row.id)},'#'+libraryPath(params))}>分析失败模式</button>
      </div>
    </div>
    {error ? <p className="error-inline" role="alert">{error}</p> : null}
    <p className="ws-table-scroll-hint muted">横向滑动可查看其余列</p>
    <section className="ws-surface ws-trace-list" aria-label="库内轨迹" aria-busy={loading}>
      <div className="ws-table-scroll"><table className="ws-trace-table library-table">
        <thead><tr><th scope="col"><input type="checkbox" aria-label="选择本页全部可用轨迹"
          checked={allChecked} ref={element => {if (element) element.indeterminate = someChecked && !allChecked}}
          disabled={!selectable.length || !allChecked && ids.size + unselected.length > libraryLimit}
          onChange={event => togglePage(event.target.checked)}/></th>
          <th scope="col">轨迹 / 来源</th><th scope="col">业务结果</th><th scope="col">诊断状态</th>
          <th scope="col">关联任务</th><th scope="col">入库时间</th></tr></thead>
        <tbody>{data?.items.map(row => {
          const known = row.outcome !== 'unknown'
          return <tr key={row.id}>
            <td><input type="checkbox" aria-label={`选择 ${row.title}`} checked={ids.has(row.id)}
              disabled={!known || atLimit && !ids.has(row.id)}
              onChange={event => onSelect(event.target.checked ? [...selected, row] : selected.filter(item => item.id !== row.id))}/></td>
            <td><a className="ws-trace-title" href={'#' + libraryPath(params, row.id)}>{row.title}</a>
              <small>{formatLabel(row.input_format ?? '')} · {row.steps} 条消息</small>
              {row.source ? <small className="library-source" title={sourceLabel(row.source)}>{sourceLabel(row.source)}</small> : null}</td>
            <td><Badge value={row.outcome}>{businessOutcomes[row.outcome as keyof typeof businessOutcomes]}</Badge>
              {known ? null : <small>重新上传并填写业务结果后可用于新任务</small>}</td>
            <td><Badge value={row.diagnosis}>{libraryDiagnosis[row.diagnosis as keyof typeof libraryDiagnosis]}</Badge></td>
            <td>{row.task_count} 个</td><td>{dateLabel(row.created_at)}</td>
          </tr>
        })}</tbody>
      </table></div>
      {!data?.items.length ? <div className="empty">{loading ? '正在读取轨迹…' : error ?
        <button className="secondary" onClick={refresh}>重新加载</button> : hasFilters ? '没有符合当前筛选条件的轨迹。' : <><p>轨迹库暂无数据</p><a href="#/tasks/new">新建任务并上传轨迹</a></>}</div> : null}
      <Pagination page={data?.page ?? 1} pages={data?.pages ?? 1} pageSize={size} total={data?.total ?? 0}
        loading={!data} onPage={next => {if (next !== data?.page) update({page: String(next)})}}
        onSize={next => update({page_size: String(next), page: ''})}/>
    </section>
  </>
}
