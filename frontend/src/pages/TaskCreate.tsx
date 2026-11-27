import DiagnosisDepthField, {defaultDiagnosisLevel, diagnosisDepthLabel, diagnosisLevelFor} from '../components/DiagnosisDepthField'
import DiagnosisModelField, {useDiagnosisModels} from '../components/DiagnosisModelField'
import type {DiagnosisLevel} from '../types'
import {useEffect,useRef,useState} from 'react'
import {Upload,FileCheck,ArrowLeft} from 'lucide-react'
import {uiText} from '../branding'
import {api,post} from '../api'
import type {LibrarySelection} from '../traceLibrary'
import {formatLabel,useResource,useWorkspace} from '../workspace'
import type {AnalysisTask,ImportPreview} from '../workspace'

export default function TaskCreate({source,library}:{source:string|null;library?:{items:LibrarySelection[];back:string;onCreated:()=>void}}){
  const {busy,action,go}=useWorkspace(),{data:original,error:sourceError}=useResource<AnalysisTask>(source?`/tasks/${source}`:null)
  const today=new Date().toLocaleDateString('sv-SE')
  const [name,setName]=useState(`轨迹分析 · ${today}`),[promptLanguage,setPromptLanguage]=useState<'en'|'zh'>('zh')
  const [diagnosisMode,setDiagnosisMode]=useState<DiagnosisLevel>(defaultDiagnosisLevel)
  const [modelId,setModelId]=useState('')
  const models=useDiagnosisModels(modelId)
  const canStart=!!models.selected&&!models.error
  const [files,setFiles]=useState<File[]>([]),[preview,setPreview]=useState<ImportPreview|null>(null),[excluded,setExcluded]=useState<string[]>([])
  const [fallback,setFallback]=useState(''),[autoFeedback,setAutoFeedback]=useState(false),[parsing,setParsing]=useState(false),[error,setError]=useState('')
  const [submitError,setSubmitError]=useState('')
  const input=useRef<HTMLInputElement>(null),loadedSource=useRef(''),generation=useRef(0)
  useEffect(()=>{if(original&&loadedSource.current!==original.id){loadedSource.current=original.id;setName(original.name.slice(0,95)+' · 副本');setPromptLanguage(original.prompt_language??'en');setDiagnosisMode(diagnosisLevelFor(original.diagnosis_mode))}},[original])
  useEffect(()=>()=>{generation.current++},[])
  async function parse(next:File[], nextExcluded:string[]) {
    setSubmitError('')
    if(!next.length){
      setFiles([]);setPreview(null);setExcluded([]);setFallback('');setError('')
      return
    }
    const id=++generation.current
    setParsing(true)
    setError('')
    try {
      const form=new FormData()
      next.forEach(file=>form.append('files',file))
      const data=await api<ImportPreview>('/import/preview',{method:'POST',body:form})
      if(id!==generation.current)return
      setFiles(next)
      setPreview(data)
      setExcluded(nextExcluded)
      if(!files.length)setFallback('')
    } catch(e) {
      if(id===generation.current)setError((e as Error).message)
    } finally {
      if(id===generation.current)setParsing(false)
    }
  }
  function removeFile(id:string) {
    const index=Number(id)
    const remaining=files.filter((_,i)=>i!==index)
    const remapped=excluded.filter(key=>Number(key)!==index).map(key=>String(Number(key)-(Number(key)>index?1:0)))
    void parse(remaining,remapped)
  }
  const seen=new Set<string>()
  const included=preview?.files.filter(f=>!excluded.includes(f.id))??[]
  const includedRows=(preview?.rows??[]).filter(r=>!excluded.includes(r.file_id))
  const rows=includedRows.filter(r=>{const key=r.outcome==='unknown'&&fallback?r.fingerprints[fallback]||r.id:r.id;if(seen.has(key))return false;seen.add(key);return true})
  const unknown=rows.filter(r=>r.outcome==='unknown'&&!fallback).length
  const hasUnknown=includedRows.some(row=>row.outcome==='unknown')
  const invalid=included.filter(f=>f.errors.length).length
  const total=library?library.items.length:source?original?.input_count??0:rows.length
  const valid=!!name.trim()&&total>0&&!unknown&&!invalid&&!parsing&&(!source||!!original&&!sourceError)
  const failed=library?library.items.filter(r=>r.outcome==='failed').length:source?original?.counts.business_failed??0:rows.filter(r=>r.outcome==='failed'||r.outcome==='unknown'&&fallback==='failed').length
  const succeeded=library?library.items.filter(r=>r.outcome==='succeeded').length:source?original?.counts.business_succeeded??0:rows.filter(r=>r.outcome==='succeeded'||r.outcome==='unknown'&&fallback==='succeeded').length
  const back=library?.back??'#/tasks'
  async function create(start:boolean){
    if(!valid||busy||(start&&!canStart))return
    setSubmitError('')
    const inputs=library?{trace_ids:library.items.map(row=>row.id)}:source?{source_task_id:source}:{preview_id:preview!.id,excluded_files:excluded,unknown_outcome:fallback}
    const result=await action(()=>post<{task_id:string;run_id:string|null}>('/tasks',{name,prompt_language:promptLanguage,diagnosis_mode:diagnosisMode,model_connection_id:models.selected?.id,
      ...inputs,start,auto_feedback:autoFeedback}),start?'任务已创建，轨迹已加入分析队列':'草稿已保存，尚未启动诊断',setSubmitError)
    if(result){library?.onCreated();go(`/tasks/${result.task_id}${result.run_id?'?run='+result.run_id:''}`)}
  }
  return <><div className="ws-breadcrumb"><a href={back}>{library?'轨迹库':'分析任务'}</a><span>/</span><span>新建任务</span></div><div className="ws-heading"><div><h1>新建分析任务</h1><p>添加待诊断的轨迹，校验通过后可立即开始分析或保存草稿。</p></div><a className="secondary" href={back}><ArrowLeft size={15}/>{library?'返回轨迹库':'返回分析任务'}</a></div>
  <div className="ws-create-layout"><div><section className="ws-surface ws-form-section"><h2><span>1</span>任务信息</h2><label>任务名称<input value={name} maxLength={100} onChange={e=>setName(e.target.value)} required/></label></section>
  <section className="ws-surface ws-form-section"><h2><span>2</span>{library?'确认库内轨迹':'上传轨迹'}</h2>{library?<>
    {library.items.length?<><div className="ws-info"><FileCheck size={18}/><span>来自轨迹库的 {total} 条轨迹。新任务将单独保存诊断、建议与人工复核。</span></div>
    <details className="library-inputs"><summary>查看所选轨迹（{total} 条）</summary><ul>{library.items.map(row=><li key={row.id}>{row.title} <span className="muted">· {formatLabel(row.input_format??'')}</span></li>)}</ul></details></>:<p className="ws-warning">请先在轨迹库选择本次任务的轨迹。</p>}
    <a href={back}>返回轨迹库</a>
  </>:source?<><div className="ws-info"><FileCheck size={18}/><span>来源任务：{original?.name??'正在读取…'}<br/>沿用来源任务的轨迹范围。诊断、建议和复核记录按任务独立保存。</span></div>{sourceError?<p role="alert" className="error-inline">{sourceError}</p>:null}<p className="muted">共 {total} 条轨迹 · {original?.formats.map(formatLabel).join('、')}</p><a href="#/tasks/new">上传新文件</a></>:<><p className="muted">支持 DeepAgent 轨迹 JSON 和 OTel GenAI 轨迹文件。两种来源可混合上传。</p><a className="library-source-link" href="#/library">从轨迹库选择</a><div className="ws-upload" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!parsing&&!busy&&e.dataTransfer.files.length)void parse([...files,...Array.from(e.dataTransfer.files)],excluded)}}><Upload size={26}/><strong>{parsing?'正在校验轨迹文件…':'将轨迹文件拖到此处'}</strong><span>单个文件最多 10 MB，每次上传最多 20 MB，每个任务最多 500 条轨迹</span><div className="ws-template-links" aria-label="下载轨迹模板"><span>没有轨迹文件？下载模板</span><a href="/templates/deepagent-template.json" download>DeepAgent JSON</a><a href="/templates/otel-genai-template.json" download>OTel GenAI</a><a href="/templates/otel-genai-batch-template.json" download>OTel GenAI 批量</a></div><input ref={input} hidden aria-label="选择轨迹文件" type="file" multiple accept=".json,.jsonl,application/json,application/x-ndjson" onChange={e=>{const next=Array.from(e.target.files??[]);if(next.length)void parse([...files,...next],excluded);e.target.value=''}}/><div className="ws-actions"><button className="primary" disabled={parsing||busy} onClick={()=>input.current?.click()}>{files.length?'添加文件':'选择文件'}</button></div></div><p className="ws-help">上传和校验在本地完成。缺少业务结果的轨迹需要在保存前补齐。</p>
  {error?<p role="alert" className="error-inline">{error}</p>:null}
  {preview?<><div className="ws-preview-summary" role="status"><span><b>{total}</b> 条有效轨迹</span><span>{includedRows.length-rows.length} 条重复，自动跳过</span><span className={unknown?'ws-error':''}>{unknown} 条结果未知</span><span className={invalid?'ws-error':''}>{invalid} 个文件有错误</span></div><div className="ws-table-scroll"><table className="ws-file-table"><thead><tr><th>文件及格式</th><th>识别条数</th><th>校验结果</th><th>操作</th></tr></thead><tbody>{preview.files.map(f=><tr key={f.id} className={excluded.includes(f.id)?'ws-excluded':''}><td>{f.name}<small>{f.formats.map(formatLabel).join('、')}</small></td><td>{f.count} 条</td><td>{excluded.includes(f.id)?'已排除':f.errors.length?<div className="ws-error">{uiText(f.errors[0])}</div>:<BadgeOk/>}</td><td><button className="text-button" aria-label={`${excluded.includes(f.id)?'重新加入':'排除文件'} ${f.name}`} disabled={parsing||busy} onClick={()=>setExcluded(old=>old.includes(f.id)?old.filter(x=>x!==f.id):[...old,f.id])}>{excluded.includes(f.id)?'重新加入':'排除文件'}</button>{files.length?<button type="button" className="text-button ws-remove-file" aria-label={`移除 ${f.name}`} disabled={parsing||busy} onClick={()=>removeFile(f.id)}>移除</button>:null}</td></tr>)}</tbody></table></div>{hasUnknown?<><label className="ws-outcome-label">补充缺失的业务结果<select value={fallback} onChange={e=>setFallback(e.target.value)}><option value="">请选择业务结果</option><option value="failed">将未知项标记为业务失败</option><option value="succeeded">将未知项标记为业务成功</option></select></label><p className="ws-help">此设置仅应用于缺少业务结果的轨迹。</p></>:null}</>:null}</>}
  </section><section className="ws-surface ws-form-section"><h2><span>3</span>分析配置</h2><DiagnosisDepthField value={diagnosisMode} onChange={setDiagnosisMode} disabled={busy}/><DiagnosisModelField options={models} onChange={setModelId} disabled={busy}/><p className="ws-help">每次启动分析时，系统会保存当次使用的模型和运行参数。</p><fieldset className="ws-choice-group"><legend>提示词语言</legend><label className="ws-radio"><input type="radio" name="prompt-language" value="zh" checked={promptLanguage==='zh'} onChange={()=>setPromptLanguage('zh')}/><span>中文<small>使用中文诊断指令，保留 Agent、API、JSON、taxonomy_tag 等通用术语。</small></span></label><label className="ws-radio"><input type="radio" name="prompt-language" value="en" checked={promptLanguage==='en'} onChange={()=>setPromptLanguage('en')}/><span>English<small>使用英文诊断提示词。</small></span></label></fieldset><label className="ws-check"><input type="checkbox" checked={autoFeedback} onChange={e=>setAutoFeedback(e.target.checked)}/><span>诊断完成后自动生成改进建议<small>对已定位关键失败步骤的轨迹逐条生成建议，会额外调用模型。</small></span></label></section></div>
  <aside className="ws-surface ws-start-summary"><span className="ws-eyebrow">本次任务</span><h2>{name||'未命名任务'}</h2><dl><dt>轨迹数量</dt><dd>{total} 条</dd><dt>业务失败 / 成功</dt><dd>{failed} / {succeeded}</dd><dt>诊断等级</dt><dd>{diagnosisDepthLabel(diagnosisMode)}</dd><dt>诊断模型</dt><dd>{models.selected?.model??'未选择'}</dd><dt>提示词语言</dt><dd>{promptLanguage==='zh'?'中文':'English'}</dd><dt>建议生成</dt><dd>{autoFeedback?'诊断后自动':'按需生成'}</dd></dl><p className={invalid||unknown?'ws-error':'muted'}>{parsing?'正在校验数据…':invalid?'请修正或排除有格式错误的文件。':!total?'请选择至少一条通过校验的轨迹。':unknown?'请补齐未知业务结果。':!name.trim()?'请填写任务名称。':'轨迹已就绪。'}</p>{submitError?<p className="error-inline" role="alert">{submitError}</p>:null}<div className="ws-start-actions"><button className="primary" disabled={!valid||busy||!canStart} onClick={()=>void create(true)}>{busy?'正在创建…':'创建并开始分析'}</button><button className="secondary" disabled={!valid||busy} onClick={()=>void create(false)}>保存草稿</button></div><p className="ws-help">开始分析后，所选轨迹将发送至配置的模型服务。</p></aside></div></>
}
function BadgeOk(){return <span className="ws-badge completed">校验通过</span>}
