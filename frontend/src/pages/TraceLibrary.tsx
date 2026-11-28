import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {Play, RefreshCw, Search, Upload} from 'lucide-react'
import {playgroundLink} from '../playground'
import {api, dateLabel} from '../api'
import {sourceLabel} from '../branding'
import DeleteRecord, {DeleteRecords, traceDeletionBlock} from '../components/DeleteRecord'
import Pagination from '../components/Pagination'
import TableSelection, {useSelectionRequest} from '../components/TableSelection'
import {businessOutcomes, isAnalyzing, libraryDiagnosis, libraryFilterQuery, libraryLimit, libraryPath, openLibraryTask} from '../traceLibrary'
import type {LibraryPage, LibrarySelection} from '../traceLibrary'
import {Badge, formatLabel, useResource, useWorkspace} from '../workspace'

const poll = (data: LibraryPage) => data.items.some(row => isAnalyzing(row.diagnosis)) ? 2500 : 0
const statusFilters = [['','全部'], ['pending','待分析'], ['active','分析中'], ['completed','已分析'], ['failed','分析失败']]

export default function TraceLibrary({params, selected, onSelect}: {
  params: URLSearchParams; selected: LibrarySelection[]; onSelect: (items: LibrarySelection[]) => void
}) {
  const {go, version, refresh, notify, busy} = useWorkspace()
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
  const filterQuery = libraryFilterQuery(params)
  const query = new URLSearchParams(filterQuery)
  query.set('page', String(page))
  query.set('page_size', String(size))
  const {data, loading, error} = useResource<LibraryPage>(`/trace-library?${query}`, version, poll)
  const tableViewport = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (tableViewport.current) tableViewport.current.scrollTop = 0
  }, [filterQuery, page, size])
  const allSelection = useSelectionRequest(`/trace-library?selection=true&${filterQuery}`)

  const ids = new Set(selected.map(row => row.id))
  const rows = data?.items ?? []
  const selectionBusy = busy || allSelection.loading
  const currentStates = new Map(rows.map(row => [row.id,row.diagnosis]))
  const reuseBlock = selected.length > libraryLimit ? '每次最多分析 500 条轨迹' :
    selected.some(row => row.outcome === 'unknown') ? '请先补齐所选轨迹的业务结果' :
      selected.some(row => isAnalyzing(currentStates.get(row.id) ?? row.diagnosis)) ? '请取消选择仍在排队或分析中的轨迹' : ''
  const hasFilters = !!filterQuery

  function update(patch: Record<string, string>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    if (libraryFilterQuery(next) !== filterQuery) onSelect([])
    go(libraryPath(next))
  }

  function togglePage(checked: boolean) {
    const pageIds = new Set(rows.map(row => row.id))
    onSelect(checked ? [...selected, ...rows.filter(row => !ids.has(row.id))] : selected.filter(row => !pageIds.has(row.id)))
  }

  function removeSelection(deletedIds: string[]) {
    const deleted = new Set(deletedIds)
    onSelect(selected.filter(row => !deleted.has(row.id)))
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
      <div><h1>轨迹库</h1><p>导入轨迹、启动分析，或直接查看已有的诊断结果。</p></div>
      <div className="ws-actions"><input ref={importInput} hidden type="file" multiple accept=".json,.jsonl,application/json,application/x-ndjson"
        aria-label="选择要导入的轨迹文件" onChange={event => {void importFiles(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
        <button className="secondary" disabled={importing} onClick={() => importInput.current?.click()}><Upload size={15}/>{importing ? '正在导入…' : '导入轨迹'}</button>
        <button className="secondary" onClick={refresh}><RefreshCw size={15}/>刷新</button></div>
    </div>
    {importError ? <p className="error-inline" role="alert">{importError}</p> : null}
    <div className="ws-segments library-status-filters" aria-label="按分析状态筛选" role="group">
      {statusFilters.map(([value,label]) => {
        const active = filters.diagnosis === value || value === 'active' && isAnalyzing(filters.diagnosis)
        return <button type="button" key={value} aria-pressed={active} className={active ? 'active' : undefined}
          onClick={() => update({diagnosis:value, page:''})}>{label}</button>
      })}
    </div>
    <form className="ws-filters library-filters" onSubmit={event => {
      event.preventDefault()
      update({search: searchInput.trim(), page: ''})
    }}>
      <input type="search" aria-label="搜索轨迹" placeholder="标题、Agent、轨迹 ID 或来源" maxLength={200}
        value={searchInput} onChange={event => setSearchInput(event.target.value)}/>
      <button className="secondary" type="submit"><Search size={15}/>搜索</button>
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
      <TableSelection pageIds={rows.map(row => row.id)} selectedIds={ids} total={data?.total ?? 0}
        disabled={selectionBusy || !data} loading={allSelection.loading} onPage={togglePage}
        onAll={checked => checked ? void allSelection.load<LibrarySelection>(onSelect) : onSelect([])}/>
      <strong aria-live="polite">已选 {selected.length} 条</strong>
      {reuseBlock ? <span>{reuseBlock}</span> : null}
      <div className="ws-actions">
        <button className="primary small" disabled={selectionBusy || !selected.length || !!reuseBlock} onClick={() =>
          openLibraryTask(params, selected.map(row => ({...row, diagnosis:currentStates.get(row.id) ?? row.diagnosis})))}>
          <Play size={14}/>启动分析
        </button>
        <button type="button" className="secondary small" disabled={selectionBusy || selected.length !== 1}
          title={selected.length === 1 ? undefined : '请选择一条轨迹'}
          onClick={() => go(playgroundLink(selected[0].id).slice(1))}>在 Playground 调试</button>
        <DeleteRecords records={selected.map(row => ({id:row.id,name:row.title}))} kind="trace" disabled={selectionBusy} onDeleted={removeSelection}/>
      </div>
    </div>
    {allSelection.error ? <p className="error-inline" role="alert">{allSelection.error}</p> : null}
    {error ? <p className="error-inline" role="alert">{error}</p> : null}
    <p className="ws-table-scroll-hint muted">横向滑动可查看其余列</p>
    <section className="ws-surface ws-table-frame" aria-label="库内轨迹" aria-busy={loading}>
      <div ref={tableViewport} className="ws-table-scroll" role="region" aria-label="库内轨迹表格" tabIndex={0}><table className="ws-trace-table library-table">
        <thead><tr><th scope="col" aria-label="选择"/>
          <th scope="col">轨迹 / 来源</th><th scope="col">Agent</th><th scope="col">业务结果</th><th scope="col">分析状态</th>
          <th scope="col">最近关键步骤</th><th scope="col">关联任务</th><th scope="col">入库时间</th><th scope="col">操作</th></tr></thead>
        <tbody>{rows.map(row => {
          const known = row.outcome !== 'unknown'
          return <tr key={row.id}>
            <td><input type="checkbox" aria-label={`选择 ${row.title}`} checked={ids.has(row.id)}
              disabled={selectionBusy}
              onChange={event => onSelect(event.target.checked ? [...selected, row] : selected.filter(item => item.id !== row.id))}/></td>
            <td><a className="ws-trace-title" href={'#' + libraryPath(params, row.id)}>{row.title}</a>
              <small>{formatLabel(row.input_format ?? '')} · {row.steps} 条消息</small>
              {row.source ? <small className="library-source" title={sourceLabel(row.source)}>{sourceLabel(row.source)}</small> : null}</td>
            <td className="ws-agent-cell">{row.agent_name || <span className="muted">未识别</span>}</td>
            <td><Badge value={row.outcome}>{businessOutcomes[row.outcome as keyof typeof businessOutcomes]}</Badge>
              {known ? null : <small><a href={'#' + libraryPath(params, row.id)}>补充业务结果</a></small>}</td>
            <td><Badge value={row.diagnosis}>{libraryDiagnosis[row.diagnosis as keyof typeof libraryDiagnosis]}</Badge></td>
            <td>{row.critical_step != null ? '#' + row.critical_step : row.has_result && row.outcome === 'failed' ? '未定位' : '—'}</td>
            <td>{row.task_count} 个</td><td>{dateLabel(row.created_at)}</td>
            <td><DeleteRecord id={row.id} name={row.title} kind="trace" compact
              blocked={traceDeletionBlock(row.task_count, ['queued', 'running'].includes(row.diagnosis))}
              onDeleted={() => removeSelection([row.id])}/></td>
          </tr>
        })}</tbody>
      </table></div>
      {!data?.items.length ? <div className="empty">{loading ? '正在读取轨迹…' : error ?
        <button className="secondary" onClick={refresh}>重新加载</button> : hasFilters ? '没有符合当前筛选条件的轨迹。' : <p>轨迹库暂无数据，导入轨迹后即可启动分析。</p>}</div> : null}
      <Pagination page={data?.page ?? 1} pages={data?.pages ?? 1} pageSize={size} total={data?.total ?? 0}
        loading={!data} onPage={next => {if (next !== data?.page) update({page: String(next)})}}
        onSize={next => update({page_size: String(next), page: ''})}/>
    </section>
  </>
}
