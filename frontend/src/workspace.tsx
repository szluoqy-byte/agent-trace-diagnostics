import type {DiagnosisMode} from './types'
import {createContext, useContext, useEffect, useState} from 'react'
import {api} from './api'
import type {Config, Job, Trace} from './types'

export type Counts = Record<string,number>
export interface AnalysisRun {id:string; task_id:string; number:number; created_at:string; auto_feedback:boolean; config:Record<string,unknown>; counts:Counts; status:string; read_only:boolean; categories:{name:string;count:number}[]}
export interface AnalysisTask {id:string; kind?:'diagnosis'|'failure_patterns'; name:string; agent:string; version:string; goal:string; prompt_language?:'en'|'zh'; diagnosis_mode?:DiagnosisMode; created_at:string; runs:AnalysisRun[]; latest_run_id:string|null; input_count:number; formats:string[]; counts:Counts; status:string; draft_auto_feedback?:boolean; categories:{name:string;count:number}[]}
export interface TaskRow {id:string; title:string; task_id:string; source:string; dataset:string; kind:string; outcome:string; input_format:string; steps:number; position:number; job_id:string|null; diagnosis:string; stage:string|null; progress:number|null; error:string|null; attempts:number; critical_step:number|null; category:string|null; summary:string|null; feedback_status:string|null; feedback_error:string|null; feedback_applicable:boolean; review_status:string|null}
export interface RunPage {items:TaskRow[]; total:number; page:number; pages:number; page_size:number; counts:Counts; categories:{name:string;count:number}[]; read_only:boolean; status:string}
export interface TraceDetail {trace:Trace; job:Job|null; read_only:boolean; attempts:{id:string;status:string;stage:string;error:string|null;created_at:string;attempt:number}[]; navigation:{index:number;total:number;previous_id:string|null;next_id:string|null}}
export interface ImportPreview {id:string; duplicates:number; files:{id:string;name:string;errors:string[];count:number;formats:string[]}[]; rows:{id:string;file_id:string;title:string;task_id:string;outcome:string;steps:number;input_format:string;fingerprints:Record<string,string>}[]}
export interface ActionResult {enqueued:number; skipped:{trace_id:string;reason:string}[]}

export const taskStatus:Record<string,string>={draft:'草稿',pending:'待分析',running:'分析中',feedback_running:'建议生成中',completed:'已完成',partial:'部分完成',failed:'分析失败'}
export const diagnosisStatus:Record<string,string>={pending:'待诊断',queued:'排队中',running:'诊断中',completed:'已诊断',failed:'诊断失败'}
export const reviewStatus:Record<string,string>={confirmed:'已确认',modified:'已修改',rejected:'已驳回',waived:'免复核'}
export const formatLabel=(format:string)=>format==='deepagent'?'DeepAgent JSON':format==='otel_genai'?'OTel GenAI':'标准轨迹 JSON'
export function Badge({value,children}:{value:string;children?:React.ReactNode}) {return <span className={'ws-badge '+value}>{children??taskStatus[value]??value}</span>}

export function useResource<T>(path:string|null,version=0,poll:number|((data:T)=>number)=0) {
  const [resource,setResource]=useState<{path:string;data:T}|null>(null)
  const [failure,setFailure]=useState<{path:string;message:string}|null>(null)
  useEffect(()=>{
    if(!path)return
    const controller=new AbortController();let stopped=false,timer:ReturnType<typeof setTimeout>|undefined
    const tick=async()=>{
      let interval=typeof poll==='number'?poll:2000
      try{const data=await api<T>(path,{signal:controller.signal});if(typeof poll==='function')interval=poll(data);if(!stopped){setResource({path,data});setFailure(null)}}
      catch(e){if(!controller.signal.aborted&&!stopped)setFailure({path,message:(e as Error).message})}
      if(!stopped&&interval)timer=setTimeout(tick,interval)
    }
    void tick()
    return()=>{stopped=true;controller.abort();clearTimeout(timer)}
  },[path,version,poll])
  const error=failure?.path===path?failure.message:''
  return {data:resource?.path===path?resource.data:null,error,loading:!!path&&resource?.path!==path&&!error}
}
interface Workspace {openSettings:()=>void; config:Config|null; version:number; busy:boolean; action:<T>(work:()=>Promise<T>,message?:string,onError?:(message:string)=>void)=>Promise<T|undefined>; notify:(message:string)=>void; refresh:()=>void; go:(path:string,replace?:boolean)=>void}
export const WorkspaceContext=createContext<Workspace|null>(null)
export function useWorkspace(){const value=useContext(WorkspaceContext);if(!value)throw new Error('Workspace context missing');return value}
export function filterQuery(params:URLSearchParams){const q=new URLSearchParams();for(const key of ['search','diagnosis','business','review','category']){const v=params.get(key);if(v&&v!=='all')q.set(key,v)}return q.toString()}
export function taskPath(id:string,params:URLSearchParams){return `/tasks/${id}${params.size?'?'+params.toString():''}`}
export function tracePath(id:string,traceId:string,params:URLSearchParams){return `/tasks/${id}/traces/${traceId}?${params}`}
export const listScroll=new Map<string,number>()
export function listKey(id:string,params:URLSearchParams){const q=new URLSearchParams(params);q.delete('focus');q.sort();return `${id}?${q}`}
