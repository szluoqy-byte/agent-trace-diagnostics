import {Fragment, useMemo} from 'react'
import {diffChars, diffLines} from 'diff'

export default function PlaygroundDiff({before, after, originalLabel, updatedLabel}: {
  before:string; after:string; originalLabel:string; updatedLabel:string
}) {
  const blocks = useMemo(() => {
    if (before === after) return []
    const lines = diffLines(before, after, {timeout:60, maxEditLength:2500})
    const deadline = performance.now() + 30
    let characters: ReturnType<typeof diffChars> | undefined
    return lines?.map((line, index) => {
      const next = lines[index + 1]
      if (line.added) {
        const pair = characters
        characters = undefined
        return {line, characters:pair}
      }
      characters = line.removed && next?.added && line.value.length + next.value.length < 4000 && performance.now() < deadline
        ? diffChars(line.value, next.value, {timeout:12, maxEditLength:800}) : undefined
      return {line, characters}
    })
  }, [before, after])

  if (before === after) return <div className="playground-diff-empty">
    <p>内容一致</p>
    {before ? <details><summary>查看相同内容</summary><pre>{before}</pre></details> : null}
  </div>

  return <div className="playground-diff">
    <div className="playground-diff-legend">
      <span className="removed">− {originalLabel}</span><span className="added">+ {updatedLabel}</span>
    </div>
    <div className="playground-diff-blocks">
      {!blocks ? <>
        <p className="playground-comparison-note">内容较长，按原始内容和修改后内容分别展示。</p>
        <div className="playground-diff-line removed"><span aria-hidden="true">−</span><pre aria-label={originalLabel}>{before}</pre></div>
        <div className="playground-diff-line added"><span aria-hidden="true">+</span><pre aria-label={updatedLabel}>{after}</pre></div>
      </> : blocks.map(({line,characters}, index) => {
        if (!line.added && !line.removed && line.count > 6) {
          return <details key={index}><summary>展开 {line.count} 行相同内容</summary><pre>{line.value}</pre></details>
        }
        const kind = line.added ? 'added' : line.removed ? 'removed' : ''
        const parts = characters?.filter(part => line.added ? !part.removed : !part.added)
        return <div className={'playground-diff-line ' + kind} key={index}>
          <span aria-hidden="true">{line.added ? '+' : line.removed ? '−' : ''}</span>
          <pre aria-label={line.added ? updatedLabel : line.removed ? originalLabel : undefined}>
            {parts ? parts.map((part, i) => part.added || part.removed
              ? <mark key={i}>{part.value}</mark>
              : <Fragment key={i}>{part.value}</Fragment>) : line.value}
          </pre>
        </div>
      })}
    </div>
  </div>
}
