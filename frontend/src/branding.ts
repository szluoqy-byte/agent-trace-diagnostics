export const PRODUCT_NAME = '天罗智能轨迹诊断'

/** Product-owned notices are presented without implementation project names. */
export function uiText(value:string|null|undefined) {
  return (value??'').replace(/TrajDebug(?:\s*官方)?/gi,'轨迹诊断').replace(/TraceLens/gi,PRODUCT_NAME)
    .replace(/官方反馈模块/g,'改进建议').replace(/官方反馈/g,'改进建议').replace(/官方版本/g,'诊断版本')
}

export function sourceLabel(value:string) {
  if(value.includes('/THU-KEG/TrajDebug/'))return '公开评测数据'
  if(value.startsWith('TraceLens 合成示例'))return '历史合成数据'
  return value
}
