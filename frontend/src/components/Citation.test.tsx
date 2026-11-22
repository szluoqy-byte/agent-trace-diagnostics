// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import Citation from './Citation'
import type { Evidence } from '../types'

afterEach(cleanup)
const evidence:Evidence={quote:'summary claim',step:4,source_kind:'compressed',matched:true,original_matched:false,
  view_matches:[{step:4,tier:'th1',start:7,end:20},{step:5,tier:'th2',start:0,end:13}]}
const views={'4':{th1:'before summary claim after'},'5':{th2:'summary claim elsewhere'}}

it('highlights the quote in the official view and opens raw comparison without false highlighting',()=>{
  const jump=vi.fn()
  const {container}=render(<Citation evidence={evidence} views={views} onJump={jump}/>)
  expect(screen.getByText('压缩视图 · #4')).toBeTruthy()
  expect(screen.queryByText(/来源未定位/)).toBeNull()
  fireEvent.click(screen.getByText('查看引用的压缩视图'))
  expect(container.querySelector('mark')?.textContent).toBe('summary claim')
  fireEvent.click(screen.getByRole('button',{name:'对照原文'}))
  expect(jump).toHaveBeenLastCalledWith({step:4,quote:''})
  fireEvent.change(screen.getByRole('combobox',{name:'选择压缩引用位置'}),{target:{value:'1'}})
  expect(screen.getByText('压缩视图 · #5')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'对照原文'}))
  expect(jump).toHaveBeenLastCalledWith({step:5,quote:''})
})

it('keeps original and unknown quote states distinct',()=>{
  const jump=vi.fn()
  const {rerender}=render(<Citation evidence={{quote:'original',step:1,matched:true,original_matched:true,source_kind:'original'}} onJump={jump}/>)
  fireEvent.click(screen.getByRole('button',{name:'定位原文'}))
  expect(jump.mock.calls[0][0].quote).toBe('original')
  rerender(<Citation evidence={{quote:'unknown',step:null,matched:false,source_kind:'unlocated'}} onJump={jump}/>)
  expect(screen.getByText('来源未定位 · 请核对诊断结果')).toBeTruthy()
  expect(screen.queryByRole('button')).toBeNull()
})

it('uses Python Unicode offsets when the official view contains emoji',()=>{
  const e:Evidence={...evidence,view_matches:[{step:4,tier:'th1',start:1,end:14}]}
  const {container}=render(<Citation evidence={e} views={{'4':{th1:'😀summary claim after'}}} onJump={vi.fn()}/>)
  fireEvent.click(screen.getByText('查看引用的压缩视图'))
  expect(container.querySelector('mark')?.textContent).toBe('summary claim')
})
