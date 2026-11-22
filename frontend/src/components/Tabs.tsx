import type {KeyboardEvent} from 'react'

interface Props {
  id:string
  label:string
  className:string
  items:{id:string;label:string;count?:number}[]
  active:string
  onChange:(id:string)=>void
}

export default function Tabs({id,label,className,items,active,onChange}:Props) {
  function navigate(event:KeyboardEvent<HTMLButtonElement>,index:number) {
    const targets:Record<string,number> = {
      ArrowRight:(index+1)%items.length,
      ArrowLeft:(index+items.length-1)%items.length,
      Home:0,
      End:items.length-1,
    }
    const target = items[targets[event.key]]
    if(!target)return
    event.preventDefault()
    onChange(target.id)
    document.getElementById(`${id}-tab-${target.id}`)?.focus()
  }
  return <div className={className} role="tablist" aria-label={label}>
    {items.map((item,index)=><button
      type="button" role="tab" key={item.id} id={`${id}-tab-${item.id}`}
      aria-selected={active===item.id} aria-controls={`${id}-panel-${item.id}`}
      tabIndex={active===item.id?0:-1} className={active===item.id?'active':''}
      onClick={()=>onChange(item.id)} onKeyDown={event=>navigate(event,index)}>
      {item.label}{item.count!==undefined?<span>{item.count}</span>:null}
    </button>)}
  </div>
}
