import {Fragment, useMemo} from 'react'
import {diffChars, diffLines} from 'diff'

interface DiffBlock {
  kind:'added'|'removed'|''
  lines:string[]
  original?:number
  updated?:number
  inline?:Array<ReturnType<typeof diffChars> | undefined>
}

function splitLines(text:string) {
  return text ? text.replace(/\n$/, '').split('\n') : []
}

function diffBlocks(before:string, after:string) {
  const changes = diffLines(before, after, {timeout:60, maxEditLength:2500})
  const sources = changes ?? [
    {value:before, added:false, removed:true},
    {value:after, added:true, removed:false},
  ]
  let original = 1
  let updated = 1
  const blocks = sources.map((change):DiffBlock => {
    const lines = splitLines(change.value)
    const block:DiffBlock = {
      kind:change.added ? 'added' : change.removed ? 'removed' : '',
      lines,
      original:change.added ? undefined : original,
      updated:change.removed ? undefined : updated,
    }
    if (!change.added) original += lines.length
    if (!change.removed) updated += lines.length
    return block
  })

  const deadline = performance.now() + 30
  for (let index = 0; index < blocks.length - 1 && performance.now() < deadline; index++) {
    const previous = blocks[index]
    const next = blocks[index + 1]
    if (previous.kind !== 'removed' || next.kind !== 'added' || previous.lines.length !== next.lines.length) continue
    const inline = previous.lines.map((line, row) =>
      line.length + next.lines[row].length < 4000 && performance.now() < deadline
        ? diffChars(line, next.lines[row], {timeout:12, maxEditLength:800}) : undefined,
    )
    previous.inline = next.inline = inline
  }
  return {blocks, fallback:!changes}
}

function DiffRows({block}: {block:DiffBlock}) {
  return block.lines.map((line, row) => {
    const parts = block.inline?.[row]?.filter(part => block.kind === 'added' ? !part.removed : !part.added)
    return <div className={'playground-diff-line ' + block.kind} key={row}>
      <span className="playground-diff-number">{block.original === undefined ? '' : block.original + row}</span>
      <span className="playground-diff-number">{block.updated === undefined ? '' : block.updated + row}</span>
      <span className="playground-diff-sign" aria-hidden="true">{block.kind === 'added' ? '+' : block.kind === 'removed' ? '−' : ''}</span>
      <pre>
        {parts ? parts.map((part, index) => part.added || part.removed
          ? <mark key={index}>{part.value}</mark>
          : <Fragment key={index}>{part.value}</Fragment>) : line}
      </pre>
    </div>
  })
}

export default function PlaygroundDiff({before, after, originalLabel, updatedLabel}: {
  before:string; after:string; originalLabel:string; updatedLabel:string
}) {
  const {blocks, fallback} = useMemo(() => diffBlocks(before, after), [before, after])
  return <div className="playground-diff">
    {before === after ? <p className="playground-comparison-note">内容一致</p>
      : <div className="playground-diff-legend">
        <span className="removed">− {originalLabel}</span><span className="added">+ {updatedLabel}</span>
      </div>}
    <div className="playground-diff-blocks">
      <div className="playground-diff-header"><span>原始行</span><span>新行</span></div>
      {fallback ? <p className="playground-comparison-note">内容较长，分别显示原始请求和更新后的请求。</p> : null}
      {blocks.map((block, index) => !block.kind && block.lines.length > 6
        ? <details key={index}>
          <summary>展开 {block.lines.length} 行相同内容</summary><DiffRows block={block}/>
        </details>
        : <DiffRows key={index} block={block}/>)}
    </div>
  </div>
}
