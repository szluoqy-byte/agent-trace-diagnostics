import {uiText} from './branding'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init)
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : '输入格式不符合要求，请检查步骤和必填内容' } catch { /* retain HTTP status */ }
    throw new Error(uiText(message))
  }
  return response.json() as Promise<T>
}
export const post = <T,>(path: string, data?: unknown) => api<T>(path, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data ?? {}) })
export const put = <T,>(path: string, data?: unknown) => api<T>(path, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data ?? {}) })
export function dateLabel(value: string) { return new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}).format(new Date(value)) }
