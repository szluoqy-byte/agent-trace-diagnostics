export function playgroundLink(traceId: string, spanId?: string) {
  const params = new URLSearchParams({trace:traceId})
  if (spanId) params.set('span', spanId)
  return '#/playground?' + params
}

export const responseLabels: Record<string,string> = {
  content:'返回内容', refusal:'拒绝原因', tool_calls:'工具调用', function_call:'函数调用',
}

function isRecord(value:unknown): value is Record<string,unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function sortKeys(value:unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys)
  if (!isRecord(value)) return value
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortKeys(value[key])]))
}

export function display(value:unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

export function describeRequest(input:string) {
  try {
    const value:unknown = JSON.parse(input)
    if (!isRecord(value) || !Array.isArray(value.messages)) return null
    const {messages, ...options} = value
    const sections = messages.map((message:unknown) => {
      if (!isRecord(message)) return display(sortKeys(message))
      const {role, content, ...fields} = message
      return [
        '[' + (typeof role === 'string' ? role : '消息') + ']',
        content != null ? display(sortKeys(content)) : '',
        Object.keys(fields).length ? display(sortKeys(fields)) : '',
      ].filter(Boolean).join('\n')
    })
    if (Object.keys(options).length) sections.push('[其他请求参数]\n' + display(sortKeys(options)))
    return sections.join('\n\n')
  } catch {
    return null
  }
}

export function describeOutput(message:Record<string,unknown>) {
  return Object.entries(responseLabels)
    .filter(([field]) => message[field] != null && message[field] !== '')
    .map(([field,label]) => '[' + label + ']\n' + display(sortKeys(message[field])))
    .join('\n\n')
}
