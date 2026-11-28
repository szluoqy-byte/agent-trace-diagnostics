export function playgroundLink(traceId: string, spanId?: string) {
  const params = new URLSearchParams({trace:traceId})
  if (spanId) params.set('span', spanId)
  return '#/playground?' + params
}
