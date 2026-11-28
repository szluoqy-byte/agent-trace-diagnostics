// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import {api,post} from './api'
import TraceViewer from './components/TraceViewer'

const fixtures = vi.hoisted(() => {
  const ids=['first','second','third',...Array.from({length:9},(_,i)=>`trace${i+4}`)]
  const traces=ids.map((id,i)=>({id,title:`轨迹 ${id}`,created_at:'2026-11-18T06:00:00Z',dataset:'test',task_id:id,outcome:'failed',kind:'imported',source:'test',steps:i+1,position:i,job_id:null,diagnosis:i<2?'completed':'pending',attempts:0,critical_step:i===0?0:null,category:i===0?'fixture-taxonomy':null,review_status:null,feedback_applicable:false,input_format:'messages'}))
  const data=Object.fromEntries(traces.map((t,i)=>[t.id,{id:t.id,data:{title:t.title,task_description:'task',messages:Array.from({length:i+1},(_,step)=>({step,role:'assistant',name:'',source_step:String(step),content:`${t.id} 的原始消息 ${step}`})),annotation:{},source:'test',kind:t.kind,outcome:'failed'}}]))
  const counts={total:12,pending:10,completed:2,business_failed:12}
  const run={id:'r1',number:1,task_id:'one',created_at:'2026-11-18T06:00:00Z',auto_feedback:false,config:{model:'fixture',concurrency:2},counts,status:'pending',read_only:false,categories:[]}
  const task={id:'one',name:'任务 one',agent:'Agent',version:'v1',goal:'排查',created_at:run.created_at,runs:[run],latest_run_id:'r1',input_count:12,formats:['messages'],counts,status:'pending',categories:[]}
  return {traces,data,task}
})
vi.mock('./api', async importOriginal=>({...await importOriginal<typeof import('./api')>(),post:vi.fn(),api:vi.fn(async(path:string)=>{
  const url=new URL(path,'http://localhost'),parts=url.pathname.split('/').filter(Boolean)
  if(path==='/config')return {ready:true,model:'fixture',base_url:'http://localhost/v1',has_key:false,concurrency:2,max_retries:0,engine:'test'}
  if(path==='/model-configurations')return {items:[{id:'configured',name:'Fixture',model:'fixture',is_default:true}]}
  if(path==='/tasks')return [fixtures.task]
  if(path==='/tasks/one')return fixtures.task
  if (url.pathname === '/trace-library') {
    const status = url.searchParams.get('diagnosis')
    const rows = fixtures.traces.filter(row => !status || row.diagnosis === status)
    return {items:rows.slice(0,10), total:rows.length, page:1, pages:Math.max(1, Math.ceil(rows.length / 10)), page_size:10}
  }
  if(parts[4]==='traces'&&parts[5]){const id=parts[5],index=fixtures.traces.findIndex(t=>t.id===id);return {trace:fixtures.data[id],job:null,read_only:false,attempts:[],navigation:{index,total:12,previous_id:fixtures.traces[index-1]?.id??null,next_id:fixtures.traces[index+1]?.id??null}}}
  if(parts[4]==='traces'){
    const rows=fixtures.traces.filter(t=>!url.searchParams.get('search')||t.title.includes(url.searchParams.get('search')!)),page=Number(url.searchParams.get('page')||1),size=Number(url.searchParams.get('page_size')||10)
    if(url.searchParams.get('selection')==='true')return {items:rows.map(row=>row.id)}
    return {items:rows.slice((page-1)*size,page*size),total:rows.length,page,pages:Math.max(1,Math.ceil(rows.length/size)),page_size:size,counts:fixtures.task.counts,categories:[],read_only:false,status:'pending'}
  }
  throw new Error('Unexpected API: '+path)
})}))
beforeEach(()=>{Element.prototype.scrollIntoView=vi.fn();window.scrollTo=vi.fn()})
afterEach(()=>{cleanup();window.history.replaceState(null,'','#/tasks')})

it('opens the library by default and launches analysis for selected pending traces', async () => {
  window.history.replaceState(null, '', '/')
  render(<App/>)
  await screen.findByRole('heading', {name:'轨迹库'})
  expect(screen.getByRole('link', {name:'轨迹库'}).getAttribute('aria-current')).toBe('page')
  fireEvent.click(screen.getByRole('button', {name:'待分析'}))
  await waitFor(() => expect(screen.queryByRole('link', {name:'轨迹 first'})).toBeNull())
  fireEvent.click(await screen.findByRole('checkbox', {name:'选择 轨迹 third'}))
  fireEvent.click(screen.getByRole('button', {name:'启动分析'}))
  await screen.findByRole('heading', {name:'启动分析'})
  await waitFor(() => expect((screen.getByRole('button', {name:'启动分析'}) as HTMLButtonElement).disabled).toBe(false))
  vi.mocked(post).mockResolvedValueOnce({task_id:'one',run_id:'r1'})
  fireEvent.click(screen.getByRole('button', {name:'启动分析'}))
  await waitFor(() => expect(location.hash).toBe('#/tasks/one?run=r1'))
  expect(post).toHaveBeenLastCalledWith('/tasks', expect.objectContaining({
    trace_ids:['third'], start:true, model_connection_id:'configured', reject_active:true,
  }))
})

it('returns from system settings with the unsaved task form intact', async () => {
  window.history.replaceState(null,'','#/tasks/new')
  render(<App/>)
  const name = screen.getByRole('textbox', {name:'任务名称'}) as HTMLInputElement
  fireEvent.change(name, {target:{value:'排查线上审批轨迹'}})
  fireEvent.click(screen.getByRole('link', {name:'系统设置'}))
  expect(location.hash).toBe('#/settings')
  await screen.findByRole('heading', {name:'系统设置'})
  expect(screen.queryByRole('textbox', {name:'任务名称'})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'返回上一页'}))
  expect((await screen.findByRole('textbox', {name:'任务名称'}) as HTMLInputElement).value).toBe('排查线上审批轨迹')
})

it('switching task trajectories repeatedly keeps one matching log and inspector',async()=>{
  window.history.replaceState(null,'','#/tasks/one/traces/first?run=r1')
  const {container}=render(<App/>);await screen.findByText('first 的原始消息 0')
  for(const id of ['second','third','first','third','second','first']){
    act(()=>{window.location.hash=`/tasks/one/traces/${id}?run=r1`;window.dispatchEvent(new Event('hashchange'))})
    await screen.findByText(`${id} 的原始消息 0`)
    await waitFor(()=>{expect(container.querySelectorAll('.trace-panel')).toHaveLength(1);expect(container.querySelectorAll('.inspector')).toHaveLength(1)})
    expect(container.querySelectorAll('.trace-panel article')).toHaveLength(fixtures.data[id].data.messages.length)
    for(const other of ['first','second','third'].filter(t=>t!==id))expect(container.querySelector('.trace-panel')!.textContent).not.toContain(`${other} 的原始消息`)
  }
})

it('opens the current result when an old task history URL is used', async () => {
  window.history.replaceState(null,'','#/tasks/one?tab=history&run=old&category=fixture-taxonomy')
  render(<App/>)
  expect(await screen.findByRole('link', {name:'轨迹 first'})).toBeTruthy()
  expect(location.hash).toBe('#/tasks/one')
  expect(screen.queryByRole('tab', {name:/分析记录/})).toBeNull()
  expect(screen.queryByRole('combobox', {name:/分析记录/})).toBeNull()
  expect(screen.getByRole('link', {name:'导出当前分析'}).getAttribute('href')).toBe('/api/tasks/one/runs/r1/export')
  expect(screen.getByRole('columnheader', {name:'关键步骤'})).toBeTruthy()
  expect(within(screen.getByRole('row', {name:/轨迹 first/})).getByText('#0')).toBeTruthy()
  expect(within(screen.getByRole('row', {name:/轨迹 second/})).getByText('未定位')).toBeTruthy()
  expect(screen.queryByText(/根因类别|fixture-taxonomy/)).toBeNull()
})

it('pagination preserves selected IDs across pages and clears them on filtering',async()=>{
  window.history.replaceState(null,'','#/tasks/one?run=r1')
  const {container}=render(<App/>);await screen.findByRole('link',{name:'轨迹 first'})
  expect(container.querySelectorAll('.ws-trace-table tbody tr')).toHaveLength(10)
  fireEvent.click(screen.getByLabelText('选择轨迹 first'))
  fireEvent.click(screen.getByRole('button',{name:/^下一页$/}))
  await screen.findByRole('link',{name:'轨迹 trace11'})
  expect(container.querySelectorAll('.ws-trace-table tbody tr')).toHaveLength(2)
  expect(screen.getByText('已选 1 条')).toBeTruthy()
  fireEvent.click(screen.getByLabelText('选择轨迹 trace11'))
  expect(screen.getByRole('link',{name:'导出所选'}).getAttribute('href')).toContain('trace_id=first&trace_id=trace11')
  const pageSelection=screen.getByRole<HTMLInputElement>('checkbox',{name:'全选本页'})
  fireEvent.click(pageSelection)
  expect(await screen.findByText('已选 3 条')).toBeTruthy()
  fireEvent.click(pageSelection)
  expect(await screen.findByText('已选 1 条')).toBeTruthy()
  const allSelection=screen.getByRole<HTMLInputElement>('checkbox',{name:'跨页全选'})
  fireEvent.click(allSelection)
  expect(await screen.findByText('已选 12 条')).toBeTruthy()
  expect(allSelection.checked && pageSelection.checked).toBe(true)
  expect(screen.queryByRole('button',{name:'清空选择'})).toBeNull()
  fireEvent.click(allSelection)
  expect(await screen.findByText('已选 0 条')).toBeTruthy()
  fireEvent.click(screen.getByLabelText('选择轨迹 trace11'))
  fireEvent.change(screen.getByLabelText('搜索轨迹'),{target:{value:'second'}})
  await screen.findByRole('link',{name:'轨迹 second'})
  expect(screen.getByText('已选 0 条')).toBeTruthy()
  expect(screen.getByLabelText('分页状态').textContent).toBe('第 1 / 1 页')
})

it('posts cross-page reviews once and reloads the current task list', async () => {
  window.history.replaceState(null,'','#/tasks/one')
  vi.mocked(post).mockResolvedValueOnce({updated:11,skipped:[{trace_id:'third',title:'轨迹 third',reason:'尚未完成诊断'}]})
  render(<App/>)
  await screen.findByRole('link',{name:'轨迹 first'})
  fireEvent.click(screen.getByRole('checkbox',{name:'跨页全选'}))
  await screen.findByText('已选 12 条')
  fireEvent.click(screen.getByRole('button',{name:'批量复核'}))
  const dialog = within(screen.getByRole('dialog',{name:'批量复核'}))
  fireEvent.change(dialog.getByRole('combobox',{name:'复核决定'}),{target:{value:'reset'}})
  fireEvent.change(dialog.getByRole('textbox',{name:'复核说明（选填）'}),{target:{value:'需要重新核对'}})
  const before = vi.mocked(api).mock.calls.filter(([path])=>path.startsWith('/tasks/one/runs/r1/traces?page=')).length
  fireEvent.click(dialog.getByRole('button',{name:'更新复核状态'}))
  expect(await dialog.findByText('已更新 11 条轨迹，跳过 1 条。')).toBeTruthy()
  expect(dialog.getByText('尚未完成诊断')).toBeTruthy()
  expect(post).toHaveBeenCalledWith('/tasks/one/runs/r1/reviews',{
    trace_ids:fixtures.traces.map(trace=>trace.id),decision:'reset',notes:'需要重新核对',
  })
  await waitFor(()=>expect(vi.mocked(api).mock.calls.filter(([path])=>path.startsWith('/tasks/one/runs/r1/traces?page=')).length).toBeGreaterThan(before))
  fireEvent.click(dialog.getByRole('button',{name:'完成'}))
})

it('search reveals and highlights a match beyond the collapsed preview of a long message', () => {
  render(<TraceViewer messages={[{step:0,role:'tool',content:'A'.repeat(2000)+'needle-at-the-end',name:'search',source_step:'0'}]} criticalStep={null} selection={null}/>)
  expect(screen.queryByText('needle-at-the-end',{selector:'mark'})).toBeNull()
  fireEvent.change(screen.getByRole('searchbox',{name:'搜索轨迹内容'}),{target:{value:'needle-at-the-end'}})
  expect(screen.getByText('needle-at-the-end',{selector:'mark'})).toBeTruthy()
})

it('original quote jumps use Unicode offsets without shifting emoji-prefixed text',()=>{
  const {container}=render(<TraceViewer messages={[{step:0,role:'assistant',content:'😀original quote',name:'',source_step:'0'}]} criticalStep={null} selection={{step:0,quote:'original quote',start:1}}/>)
  expect(container.querySelector('mark')?.textContent).toBe('original quote')
})
