import {useState} from 'react'
import {History} from 'lucide-react'
import {dateLabel} from '../api'
import {patternTaskPath} from '../patterns'
import {libraryDiagnosis,libraryResultPath} from '../traceLibrary'
import type {LibraryHistory} from '../traceLibrary'
import {Badge,useResource,useWorkspace} from '../workspace'
import {diagnosisDepthLabel} from './DiagnosisDepthField'
import {Dialog} from './Dialogs'

export default function TraceAnalysisHistory({traceId, params, selectedId}: {
  traceId:string; params?:URLSearchParams; selectedId?:string
}) {
  const [open,setOpen] = useState(false)
  const {version} = useWorkspace()
  const {data,error,loading} = useResource<LibraryHistory>(open ? '/trace-library/' + encodeURIComponent(traceId) + '/history' : null, version)
  const close = () => setOpen(false)

  return <>
    <button type="button" className="secondary trace-info-trigger" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <History size={16} aria-hidden="true"/>分析记录
    </button>
    {open ? <Dialog title="分析记录" className="trace-info-panel" onClose={close}>
      <div className="trace-info-body trace-analysis-history" tabIndex={0} role="region" aria-label="分析记录列表">
        {loading ? <p className="muted">正在读取分析记录…</p> : error ? <p className="error-inline" role="alert">{error}</p>
          : !data?.diagnoses.length ? <p className="muted">这条轨迹暂无分析记录。</p>
          : <ul>{data.diagnoses.map(record =>
            <li key={record.job_id}>
              <div className="trace-history-title">
                <strong>{record.task_name ?? '独立分析'}</strong>
                <Badge value={record.status}>{libraryDiagnosis[record.status] ?? record.status}</Badge>
              </div>
              <small>{[dateLabel(record.created_at), record.model,
                record.diagnosis_mode && diagnosisDepthLabel(record.diagnosis_mode)].filter(Boolean).join(' · ')}</small>
              {record.job_id === selectedId ? <span className="muted">当前查看</span>
                : <a href={'#' + libraryResultPath(traceId, params ?? new URLSearchParams(), record.job_id)}
                  onClick={close}>查看本次分析</a>}
            </li>)}</ul>}
        {data?.tasks.length ? <section>
          <h3>关联任务</h3>
          <ul>{data.tasks.map(task => <li key={task.id}><a
            href={task.kind === 'failure_patterns' ? patternTaskPath(task.id) : '#/tasks/' + encodeURIComponent(task.id)}
            onClick={close}>{task.name}</a></li>)}</ul>
        </section> : null}
      </div>
    </Dialog> : null}
  </>
}
