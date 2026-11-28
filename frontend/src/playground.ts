export function playgroundLink(traceId: string, spanId?: string) {
  const params = new URLSearchParams({trace:traceId})
  if (spanId) params.set('span', spanId)
  return '#/playground?' + params
}

export const responseLabels: Record<string,string> = {
  content:'返回内容', refusal:'拒绝原因', tool_calls:'工具调用', function_call:'函数调用',
}

export function display(value:unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
