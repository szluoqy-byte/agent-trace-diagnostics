import {useState} from 'react'
import {libraryDateValue,libraryPeriods,validLibraryDates} from '../traceLibrary'
import type {libraryTimeRange} from '../traceLibrary'
import {Dialog} from './Dialogs'

type TimeRange = ReturnType<typeof libraryTimeRange>

export default function LibraryTimeFilter({time,onChange}: {time:TimeRange; onChange:(patch:Record<string,string>)=>void}) {
  const {period,range} = time
  const [custom,setCustom] = useState<{from:string; to:string}|null>(null)

  function editRange() {
    const today = new Date()
    setCustom({from:libraryDateValue(range?.start ?? today),to:libraryDateValue(range?.lastDay ?? today)})
  }

  return <div className="library-time-filter">
    <label>入库时间<select value={period} onChange={event=>{
      const value = event.target.value
      if (value === 'custom') editRange()
      else onChange({period:value === 'week' ? '' : value,date_from:'',date_to:'',page:''})
    }}>
      {libraryPeriods.map(([value,label])=><option key={value} value={value}>{label}</option>)}
    </select></label>
    {period === 'custom' && range ? <button type="button" className="text-button" onClick={editRange}>
      {libraryDateValue(range.start)} 至 {libraryDateValue(range.lastDay)}
    </button> : null}
    {custom ? <Dialog title="自定义入库时间" onClose={()=>setCustom(null)}>
      <form onSubmit={event=>{
        event.preventDefault()
        if (!validLibraryDates(custom.from,custom.to)) return
        onChange({period:'custom',date_from:custom.from,date_to:custom.to,page:''})
        setCustom(null)
      }}>
        <div className="modal-body review-form">
          <div className="form-row">
            <label>开始日期<input type="date" required value={custom.from} max={custom.to || undefined}
              onChange={event=>setCustom({...custom,from:event.target.value})}/></label>
            <label>结束日期<input type="date" required value={custom.to} min={custom.from || undefined}
              onChange={event=>setCustom({...custom,to:event.target.value})}/></label>
          </div>
        </div>
        <footer><button type="button" className="secondary" onClick={()=>setCustom(null)}>取消</button>
          <button type="submit" className="primary" disabled={!validLibraryDates(custom.from,custom.to)}>应用</button></footer>
      </form>
    </Dialog> : null}
  </div>
}
