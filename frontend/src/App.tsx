import {useCallback,useEffect,useRef,useState} from 'react'
import {Settings,X,CircleCheck,HardDrive} from 'lucide-react'
import {PRODUCT_NAME,uiText} from './branding'
import SystemSettings from './pages/SystemSettings'
import TraceMark from './components/TraceMark'
import type {Config} from './types'
import {WorkspaceContext,useResource} from './workspace'
import TaskList from './pages/TaskList'
import TaskCreate from './pages/TaskCreate'
import TaskDetail from './pages/TaskDetail'
import TrajectoryDetail from './pages/TrajectoryDetail'
import TraceLibrary from './pages/TraceLibrary'
import PatternCreate from './pages/PatternCreate'
import PatternDetail from './pages/PatternDetail'
import type {PatternScope} from './patterns'
import LibraryTraceDetail from './pages/LibraryTraceDetail'
import {libraryPath} from './traceLibrary'
import type {LibrarySelection} from './traceLibrary'

export default function App(){
  const [hash,setHash]=useState(()=>location.hash.slice(1)||'/tasks')
  const [librarySelection,setLibrarySelection]=useState<LibrarySelection[]>([])
  const [version,setVersion]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  const {data:config,error:configError}=useResource<Config>('/config',version)
  useEffect(()=>{const changed=()=>{setHash(location.hash.slice(1)||'/tasks');setError('')};window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed)},[])
  const go=useCallback((path:string,replace=false)=>{if(replace){history.replaceState(null,'','#'+path);window.dispatchEvent(new Event('hashchange'))}else location.hash=path},[])
  const openSettings=useCallback(()=>{
    if(location.hash==='#/settings')return
    history.pushState({settingsBack:{hash:location.hash.slice(1)||'/tasks',state:history.state}}, '', '#/settings')
    window.dispatchEvent(new Event('hashchange'))
  },[])
  const submitting=useRef(false)
  const closeSettings=useCallback(()=>history.back(),[])
  const refresh=useCallback(()=>setVersion(v=>v+1),[])
  const action=useCallback(async<T,>(work:()=>Promise<T>,message?:string,onError?:(message:string)=>void)=>{if(submitting.current)return undefined;submitting.current=true;setBusy(true);setError('');setNotice('');try{const result=await work();refresh();if(message)setNotice(message);return result}catch(e){(onError??setError)((e as Error).message);return undefined}finally{submitting.current=false;setBusy(false)}},[refresh])
  const settingsPage=hash==='/settings'
  const settingsBack=settingsPage?history.state?.settingsBack:null
  const workspaceHash=settingsBack?.hash??hash
  const workspaceState=settingsPage?settingsBack?.state:history.state
  const url=new URL(workspaceHash,location.origin),parts=url.pathname.split('/').filter(Boolean),id=parts[1],traceId=parts[3]
  const page=parts[0]==='library'?(id?'library-detail':'library'):parts[0]!=='tasks'?'missing':id==='new'?'new':traceId?'trace':id?'task':'tasks'
  const patterns=parts[0]==='tasks'&&id==='patterns'
  const patternId=parts[2]
  const patternSource=url.searchParams.get('source'), patternSourceRun=url.searchParams.get('run')
  const patternScope:PatternScope|null=patterns&&patternId==='new'
    ? workspaceState?.patternScope??(patternSource&&patternSourceRun?{task_id:patternSource,run_id:patternSourceRun}:null)
    : null
  const fromLibrary=page==='new'&&url.searchParams.get('from')==='library'
  const libraryActive=parts[0]==='library'||fromLibrary
  const returnParams=new URLSearchParams(url.searchParams)
  returnParams.delete('from')
  const selection:LibrarySelection[]=fromLibrary?workspaceState?.librarySelection??[]:[]
  const clearLibrarySelection=useCallback(()=>setLibrarySelection([]),[])
  const routeKey=settingsPage?'/settings':url.pathname
  useEffect(()=>{window.scrollTo(0,0)},[routeKey])
  useEffect(()=>{
    if(!notice)return
    const timer=setTimeout(()=>setNotice(''),6000)
    return()=>clearTimeout(timer)
  },[notice])
  return <WorkspaceContext value={{openSettings,config,version,busy,action,notify:setNotice,refresh,go}}><div className="task-workbench"><header className="app-header"><a className="brand" href="#/tasks"><TraceMark/><strong>{PRODUCT_NAME}</strong></a></header><div className="ws-layout"><aside className="ws-sidebar"><nav className="ws-sidebar-nav" aria-label="工作台导航"><a href="#/tasks" aria-current={!settingsPage&&parts[0]==='tasks'&&!fromLibrary?'page':undefined}>分析任务</a><a href="#/library" aria-current={!settingsPage&&libraryActive?'page':undefined}>轨迹库</a><a className="ws-sidebar-settings" href="#/settings" aria-current={settingsPage?'page':undefined} onClick={event=>{
    if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return
    event.preventDefault();openSettings()
  }}><Settings size={16}/><span>系统设置</span></a></nav></aside><main className="ws-main">{error||configError?<div role="alert" className="alert error"><span>{uiText(error||configError)}</span>{error?<button aria-label="关闭错误" onClick={()=>setError('')}><X size={16}/></button>:<button onClick={refresh}>重试</button>}</div>:null}{notice?<div role="status" className="alert notice ws-toast"><CircleCheck size={16}/><span>{uiText(notice)}</span><button aria-label="关闭提示" onClick={()=>setNotice('')}><X size={16}/></button></div>:null}
  {settingsPage?<SystemSettings onBack={settingsBack?closeSettings:undefined}/>:null}
  {!settingsPage||settingsBack?<div hidden={settingsPage}>
  {patterns&&patternId==='new'?<PatternCreate key={workspaceHash} scope={patternScope} back={workspaceState?.patternBack??(patternSource?'#/tasks/'+patternSource:'#/library')}
    taskId={url.searchParams.get('rerun')} onCreated={patternScope?.task_id||url.searchParams.has('rerun')?undefined:clearLibrarySelection}/>
    :patterns&&patternId?<PatternDetail key={patternId} id={patternId} requestedRun={url.searchParams.get('run')} requestedPattern={url.searchParams.get('pattern_id')}/>
    :page==='tasks'?<TaskList/>:page==='new'?<TaskCreate key={fromLibrary?'new-library':'new-'+(url.searchParams.get('source')||'upload')} source={fromLibrary?null:url.searchParams.get('source')}
    library={fromLibrary?{items:selection,back:'#'+libraryPath(returnParams),onCreated:clearLibrarySelection}:undefined}/>
    :page==='task'?<TaskDetail key={id} id={id} params={url.searchParams}/>:page==='trace'?<TrajectoryDetail id={id} traceId={traceId} params={url.searchParams}/>
    :page==='library'?<TraceLibrary params={url.searchParams} selected={librarySelection} onSelect={setLibrarySelection}/>
    :page==='library-detail'?<LibraryTraceDetail key={id} id={id} params={url.searchParams} onDeleted={traceId=>setLibrarySelection(items=>items.filter(item=>item.id!==traceId))}/>
    :<div className="empty"><h1>页面不存在</h1><p>请检查链接，或返回分析任务列表。</p><a href="#/tasks">返回分析任务</a></div>}</div>:null}</main></div><footer className="app-footer"><button onClick={openSettings}><i className={'dot '+(config?.ready?'completed':'idle')}/>当前模型 {config?.model||'未配置'}<span className="footer-link">设置</span></button><span><HardDrive size={14}/>数据存储在本地 <i/>轨迹并发上限 {config?.concurrency??'—'}</span></footer></div></WorkspaceContext>
}
