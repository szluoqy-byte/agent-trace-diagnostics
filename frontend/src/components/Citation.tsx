import { useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import type { CompressionViews, Evidence } from '../types'

const tiers:Record<string,string> = {th1:'高细节',th2:'中细节',th3:'概览'}
export default function Citation({evidence, views, onJump}: {evidence:Evidence; views?:CompressionViews; onJump:(e:Evidence)=>void}) {
  const [viewIndex,setViewIndex] = useState(0)
  const compressed = evidence.source_kind === 'compressed'
  const located = evidence.matched === true
  const matches = evidence.view_matches ?? []
  const location = matches[Math.min(viewIndex,Math.max(0,matches.length-1))]
  const text = location ? views?.[String(location.step)]?.[location.tier] : undefined
  const characters = text == null ? [] : Array.from(text)
  const original = !compressed && (evidence.original_matched ?? located)
  const source = compressed ? '压缩视图 · #' + (location?.step ?? evidence.step) : evidence.source_id ? '原始输入 · ' + evidence.source_id : '原文 #' + evidence.step
  return <div className={'evidence-card ' + (!located ? 'unmatched' : compressed ? 'compressed' : '')}>
    <p>“{evidence.quote || '未提供引文'}”</p>
    <div><span>{located ? source : '来源未定位 · 请核对诊断结果'}</span>
      {compressed && location ? <button className="text-button" onClick={() => onJump({step:location.step,quote:''})}>对照原文 <ArrowUpRight size={13}/></button> : original && evidence.step != null ? <button className="text-button" onClick={() => onJump(evidence)}>定位原文 <ArrowUpRight size={13}/></button> : null}
    </div>
    {compressed ? <details className="compression-source"><summary>查看引用的压缩视图</summary>
      {matches.length > 1 ? <label>匹配位置<select aria-label="选择压缩引用位置" value={Math.min(viewIndex,matches.length-1)} onChange={e=>setViewIndex(Number(e.target.value))}>{matches.map((m,i)=><option key={i} value={i}>#{m.step} · {tiers[m.tier] ?? m.tier}</option>)}</select></label> : location ? <span className="muted">#{location.step} · {tiers[location.tier] ?? location.tier}</span> : null}
      {text && location ? <pre>{characters.slice(0,location.start).join('')}<mark>{characters.slice(location.start,location.end).join('')}</mark>{characters.slice(location.end).join('')}</pre> : <p className="muted">该报告未加载对应视图，请下载压缩阶段产物查看。</p>}
      <p className="muted">这是诊断过程生成的压缩文本；可对照原始消息复核其含义。</p>
    </details> : null}
  </div>
}
