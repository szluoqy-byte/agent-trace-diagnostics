// @vitest-environment jsdom
import {afterEach, expect, it} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import TraceViewer from './TraceViewer'
import type {Trace} from '../types'

afterEach(cleanup)
const trace:Trace = {id:'test',data:{title:'OTel trace',task_description:'task',annotation:{},kind:'imported',outcome:'failed',source:'example.json',messages:[{step:0,role:'assistant',content:'answer',name:'',source_step:'original',source_refs:[{trace_id:'a'.repeat(32),span_id:'b'.repeat(16),attribute:'gen_ai.output.messages',message_index:0}]}],otel:{trace_id:'a'.repeat(32),adapter_version:'otel-genai-v1',warnings:['output missing in a later span'],spans:[{span:{spanId:'b'.repeat(16),name:'chat',status:{code:2}},resource:{},scope:{},location:'document[0].spans[0]'}]}}}

it('expands a message source to the stored span and preserves evidence jump highlighting', async () => {
  const {container} = render(<TraceViewer messages={trace.data.messages} otel={trace.data.otel} criticalStep={0} selection={{step:0,quote:'answer',start:0}}/>)
  expect(container.querySelector('mark')?.textContent).toBe('answer')
  expect(screen.queryByLabelText(`原始 span ${'b'.repeat(16)}`)).toBeNull()
  const details = container.querySelector('details')!
  details.open = true
  fireEvent(details, new Event('toggle'))
  expect((await screen.findByLabelText(`原始 span ${'b'.repeat(16)}`)).textContent).toContain('"code": 2')
  expect(screen.getByText('gen_ai.output.messages[0]')).toBeTruthy()
})
