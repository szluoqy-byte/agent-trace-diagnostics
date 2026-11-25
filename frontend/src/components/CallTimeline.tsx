interface TimelineCall {
  span_id: string
  title: string
  number: number
  start_offset_us?: number | null
  end_offset_us?: number | null
  depth?: number
}

export function durationLabel(microseconds: number | null) {
  if (microseconds == null) return '耗时未知'
  if (microseconds > 0 && microseconds < 1) return `${Number((microseconds * 1000).toFixed(2))} ns`
  if (microseconds < 1000) return `${Number(microseconds.toFixed(2))} µs`
  const seconds = microseconds >= 1_000_000
  const value = microseconds / (seconds ? 1_000_000 : 1000)
  return `${value.toLocaleString('zh-CN', {maximumFractionDigits: 2})} ${seconds ? 's' : 'ms'}`
}

export default function CallTimeline({calls, totalUs, criticalSpan, onOpen}: {
  calls: TimelineCall[]; totalUs: number; criticalSpan?: string; onOpen: (spanId: string) => void
}) {
  const axisUs = totalUs > 0 ? totalUs : 1
  return <div className="execution-waterfall">
    <div className="execution-waterfall-row execution-waterfall-heading">
      <span>调用</span>
      <div className="execution-waterfall-axis" aria-label="距轨迹开始的时间">
        {[0, 1, 2, 3, 4].map(tick => <span key={tick} style={{left: `${tick * 25}%`}}>
          {durationLabel(axisUs * tick / 4)}
        </span>)}
      </div>
      <span>耗时</span>
    </div>
    {calls.map(call => {
      const start = call.start_offset_us
      const end = call.end_offset_us
      const duration = start != null && end != null ? end - start : null
      const timing = duration != null && start != null ? `开始于 +${durationLabel(start)}，耗时 ${durationLabel(duration)}` : '区间未知'
      const title = `#${call.number} ${call.title}`
      const critical = call.span_id === criticalSpan
      return <button type="button" key={call.span_id} onClick={() => onOpen(call.span_id)}
        className={`execution-waterfall-row execution-waterfall-call${critical ? ' execution-waterfall-critical' : ''}`}
        aria-label={`${title}，${timing}${critical ? '，关键调用' : ''}`} title={`${title} · ${timing}`}>
        <span className="execution-waterfall-name" style={{paddingInlineStart: Math.min(call.depth ?? 0, 5) * 12}}>
          {title}{critical ? <small>关键调用</small> : null}
        </span>
        <span className="execution-waterfall-track" aria-hidden="true">
          {start != null && duration != null ? <span className="execution-waterfall-bar"
            style={{left: `min(${start / axisUs * 100}%, calc(100% - 3px))`, width: `${duration / axisUs * 100}%`}}/> : null}
        </span>
        <span className="execution-waterfall-duration">{durationLabel(duration)}</span>
      </button>
    })}
  </div>
}
