// 3D 追踪栏的纯函数：只做事件筛选、截断与格式化，不碰 DOM 或 Three.js。
// 这样长对局可以增量维护追踪窗口，不必在每一帧重扫全部事件。

export const TRACE_LIMIT = 20

const TRACE_TYPES = new Set([
  'turn.started',
  'action.proposed',
  'action.applied',
  'narration.line',
  'match.ended',
])

export function isTraceEvent(event) {
  if (!event || !event.type) return false
  if (event.type === 'coach.message') return event.payload?.status !== 'queued'
  return TRACE_TYPES.has(event.type)
}

/** 从事件快照里取最后一段可展示的时序，避免把整个历史带进 DOM。 */
export function makeTrace(events, limit = TRACE_LIMIT) {
  return (events || []).filter(isTraceEvent).slice(-limit)
}

/** 直播增量只追加一条，窗口满了才 slice，不再复制整段历史。 */
export function appendTrace(trace, event, limit = TRACE_LIMIT) {
  if (!isTraceEvent(event)) return trace
  const next = trace.concat(event)
  return next.length > limit ? next.slice(-limit) : next
}

/** 动作值可能是字符串，也可能是代号/问答这类对象；不能直接 String(obj)。 */
export function formatAction(value) {
  if (value == null || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  if (Array.isArray(value)) {
    return value.map(formatAction).filter(Boolean).join(' · ')
  }
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => {
        const text = formatAction(item)
        return text ? `${key}: ${text}` : key
      })
      .filter(Boolean)
      .join(' · ')
  }
  return String(value)
}

/**
 * 一条时序的短文本。translate 由页面注入，纯函数只负责按事件结构取值，
 * 保证中英文界面共享同一套顺序与截断规则。
 */
export function traceText(event, translate = (key) => key) {
  const payload = event.payload || {}
  const value = formatAction(payload.action?.value ?? payload.action?.kind ?? '')
  switch (event.type) {
    case 'turn.started': {
      const turn = payload.turnIndex ?? payload.turn
      return `${event.player || '—'} · ${turn == null ? translate('arena3d.turnStart') : `${translate('arena3d.turn')} ${turn}`}`
    }
    case 'action.proposed':
      return `${event.player || '—'} · ${value}`
    case 'action.applied':
      return `${event.player || '—'} · ${value} ✓`
    case 'coach.message':
      return `⚑ ${payload.target || '—'} · ${payload.instruction || ''}`
    case 'narration.line':
      return String(payload.text || '')
    case 'match.ended':
      return `${translate('watch.status.ended')} · ${formatAction(payload.winner ?? payload.reason ?? '')}`
    default:
      return event.type
  }
}
