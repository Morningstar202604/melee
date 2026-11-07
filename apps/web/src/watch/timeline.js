// 时间轴映射：事件序号 <-> 真实比赛时间。
//
// 为什么需要：旧时间轴按「第几条事件」排布，而一局二十问里
// 提问与思考片段的数量差一个数量级，拖动条前 90% 都在原地打转，
// 最后 10% 一下跳过整局——观众感觉"拖不动"。
//
// 这里把每条事件按 `at` 映射到 [0,1]，拖动时再换算回最近的 seq。
// 纯函数、不碰 DOM，方便在 node 里直接测。

const MS_PER_SECOND = 1000

export function toMillis(value) {
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * 为一段事件流建立时间轴索引。
 * 返回 { points, durationMs, startMs, endMs }，points 每项 { seq, ms, t }。
 * 时间解析不出来（旧数据/手写事件）时退化为按序号均匀分布，保证仍可拖动。
 */
export function buildTimeline(events) {
  const list = Array.isArray(events) ? events : []
  return timelineFromMs(list, list.map((event) => toMillis(event && event.at)))
}

function timelineFromMs(list, rawMs) {
  if (!list.length) {
    return { points: [], durationMs: 0, startMs: 0, endMs: 0, degraded: false, _rawMs: [] }
  }
  const usable = rawMs.filter((value) => value !== null)
  const startMs = usable.length ? Math.min(...usable) : 0
  const endMs = usable.length ? Math.max(...usable) : 0
  const timeline = {
    // points 是 getter：读的时候才算，算完缓存。外部代码继续写 `tl.points` 即可。
    _points: null,
    durationMs: Math.max(0, endMs - startMs),
    startMs,
    endMs,
    degraded: usable.length !== list.length,
    _rawMs: rawMs,
    _seqs: list.map((event) => event.seq),
    _count: list.length,
  }
  Object.defineProperty(timeline, 'points', {
    enumerable: true,
    get() {
      return materializePoints(timeline)
    },
  })
  return timeline
}

/** 把懒时间轴解析成 points。纯展示路径，只该在渲染/拖动时调用一次。 */
export function materializePoints(timeline) {
  if (!timeline) return []
  if (timeline._points) return timeline._points
  const raw = timeline._rawMs || []
  const count = timeline._count || 0
  const span = timeline.durationMs || 0
  const points = new Array(count)
  let lastT = -1
  for (let index = 0; index < count; index += 1) {
    const ms = raw[index]
    const offset = ms === null || ms === undefined ? 0 : Math.max(0, ms - timeline.startMs)
    let t
    if (span > 0) t = Math.min(1, offset / span)
    else t = count > 1 ? index / (count - 1) : 0
    // 时间戳必须单调；同一毫秒内的多条事件用序号微调，保证拖动位置可区分。
    if (t < lastT) t = lastT
    else if (t === lastT) t = Math.min(1, lastT + 1e-6)
    lastT = t
    points[index] = { seq: (timeline._seqs || [])[index], ms: ms === null || ms === undefined ? timeline.startMs : ms, t, index }
  }
  timeline._points = points
  return points
}

/**
 * 增量追加：只解析新事件的时间，已有的毫秒缓存直接复用。
 *
 * 旧实现每来一条事件就整条重算，一场 5000 事件的直播累计约 3.4s 主线程耗时。
 * 这里 key 在于时间戳解析（Date.parse 占 80%）不再重复：追加是 O(新增)。
 * t 的归一化推迟到 materializePoints，渲染时才做一次。
 */
export function appendToTimeline(timeline, events, nextEvents) {
  const list = Array.isArray(nextEvents) ? nextEvents : []
  if (!list.length) return timeline || timelineFromMs([], [])
  if (!timeline || !timeline._rawMs || !timeline._rawMs.length) return buildTimeline(list)

  const existing = timeline._count || 0
  if (list.length <= existing) return timeline
  const appended = list.slice(existing)
  const rawMs = timeline._rawMs.concat(appended.map((event) => toMillis(event && event.at)))
  const seqs = (timeline._seqs || []).concat(appended.map((event) => event.seq))
  const usable = rawMs.filter((value) => value !== null)
  const startMs = usable.length ? Math.min(...usable) : 0
  const endMs = usable.length ? Math.max(...usable) : 0
  const grown = {
    _points: null,
    durationMs: Math.max(0, endMs - startMs),
    startMs,
    endMs,
    degraded: usable.length !== rawMs.length,
    _rawMs: rawMs,
    _seqs: seqs,
    _count: rawMs.length,
  }
  Object.defineProperty(grown, 'points', {
    enumerable: true,
    get() {
      return materializePoints(grown)
    },
  })
  return grown
}

/** 拖动位置（0..1）→ 最近的游标（显示到第 N 条事件，1-based 语义与旧版一致）。 */
export function cursorAt(timeline, ratio) {
  const points = materializePoints(timeline)
  if (!points.length) return 0
  const clamped = Math.max(0, Math.min(1, Number(ratio) || 0))
  let best = 0
  for (let i = 0; i < points.length; i += 1) {
    if (points[i].t <= clamped + 1e-9) best = i
    else break
  }
  return best + 1
}

/** 游标（1-based）→ 时间轴比例，用于画进度条。 */
export function ratioAt(timeline, cursor) {
  const points = materializePoints(timeline)
  if (!points.length) return 0
  const index = Math.max(0, Math.min(points.length, Number(cursor) || 0)) - 1
  if (index < 0) return 0
  return points[index].t
}

/** 时间轴位置 → 比赛内耗时（毫秒），用于 "02:13 / 08:40" 这类显示。 */
export function elapsedAt(timeline, ratio) {
  const duration = (timeline && timeline.durationMs) || 0
  const clamped = Math.max(0, Math.min(1, Number(ratio) || 0))
  return Math.round(duration * clamped)
}

export function formatClock(ms) {
  const total = Math.max(0, Math.round((Number(ms) || 0) / MS_PER_SECOND))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

/** 关键事件在时间轴上的位置，用于画橙点并可点击跳转。
 *
 *  去重：切镜在一局里可能几十次，短局中相邻两个点在像素上完全重叠，
 *  叠在一起的按钮既点不中（互相 intercept），视觉上也是一坨。
 *  默认按 1.2% 的最小间距合并，保留先出现的那一个。
 */
export function marksOf(timeline, events, kinds = ['director.cue', 'match.ended'], minGap = 0.012) {
  const points = materializePoints(timeline)
  const wanted = new Set(kinds)
  const out = []
  let lastT = -Infinity
  for (let i = 0; i < points.length; i += 1) {
    const event = events[i]
    if (!event || !wanted.has(event.type)) continue
    const t = points[i].t
    if (t - lastT < minGap) {
      // 终局永远保留：它是最重要的锚点，哪怕和前一个切镜重叠。
      if (event.type !== 'match.ended') continue
    }
    lastT = t
    out.push({ seq: event.seq, t, type: event.type, index: i + 1 })
  }
  return out
}
