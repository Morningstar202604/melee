// 时间轴纯函数测试：node --test 直接跑，不需要浏览器与构建链。
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildTimeline, cursorAt, ratioAt, elapsedAt, formatClock, marksOf,
} from '../src/watch/timeline.js'

const ev = (seq, at, type = 'action.applied') => ({ seq, at, type, payload: {} })

test('按真实时间映射：中间的稀疏事件不会挤在末尾', () => {
  const events = [
    ev(0, '2026-01-01T00:00:00Z'),
    ev(1, '2026-01-01T00:00:10Z'),
    ev(2, '2026-01-01T00:01:40Z'),
  ]
  const tl = buildTimeline(events)
  assert.equal(tl.durationMs, 100_000)
  assert.equal(tl.points[1].t, 0.1)
  assert.equal(tl.points[2].t, 1)
})

test('时间缺失时退化为按序号分布，不产出 NaN', () => {
  const tl = buildTimeline([ev(0, 'bad'), ev(1, 'worse'), ev(2, '2026-01-01T00:00:02Z')])
  assert.equal(tl.degraded, true)
  assert.ok(tl.points.every((p) => Number.isFinite(p.t)))
  assert.ok(tl.points[2].t >= tl.points[1].t)
})

test('全部时间相同（批量写入）时不叠成一个点', () => {
  const same = '2026-01-01T00:00:00Z'
  const tl = buildTimeline([ev(0, same), ev(1, same), ev(2, same)])
  assert.equal(tl.durationMs, 0)
  assert.ok(tl.points[1].t > tl.points[0].t)
  assert.ok(tl.points[2].t > tl.points[1].t)
})

test('cursorAt 与 ratioAt 互为逆运算（边界与中间点）', () => {
  const tl = buildTimeline([
    ev(0, '2026-01-01T00:00:00Z'), ev(1, '2026-01-01T00:00:05Z'), ev(2, '2026-01-01T00:00:20Z'),
  ])
  assert.equal(cursorAt(tl, 0), 1)
  assert.equal(cursorAt(tl, 1), 3)
  for (const cursor of [1, 2, 3]) {
    assert.equal(cursorAt(tl, ratioAt(tl, cursor)), cursor)
  }
})

test('cursorAt 对越界与非法输入收敛', () => {
  const tl = buildTimeline([ev(0, '2026-01-01T00:00:00Z'), ev(1, '2026-01-01T00:00:01Z')])
  assert.equal(cursorAt(tl, -5), 1)
  assert.equal(cursorAt(tl, 9), 2)
  assert.equal(cursorAt(tl, NaN), 1)
  assert.equal(cursorAt({ points: [] }, 0.5), 0)
})

test('elapsedAt / formatClock 显示真实耗时', () => {
  const tl = buildTimeline([ev(0, '2026-01-01T00:00:00Z'), ev(1, '2026-01-01T00:08:40Z')])
  assert.equal(elapsedAt(tl, 0.5), 260_000)
  assert.equal(formatClock(260_000), '04:20')
  assert.equal(formatClock(0), '00:00')
  assert.equal(formatClock(undefined), '00:00')
})

test('marksOf 只挑导播切镜与终局，并带 1-based 游标', () => {
  const events = [
    ev(0, '2026-01-01T00:00:00Z', 'match.created'),
    ev(1, '2026-01-01T00:00:02Z', 'director.cue'),
    ev(2, '2026-01-01T00:00:04Z', 'action.applied'),
    ev(3, '2026-01-01T00:00:06Z', 'match.ended'),
  ]
  const tl = buildTimeline(events)
  const marks = marksOf(tl, events)
  assert.deepEqual(marks.map((m) => m.type), ['director.cue', 'match.ended'])
  assert.deepEqual(marks.map((m) => m.index), [2, 4])
})

test('marksOf 合并像素级重叠的点，避免按钮互相遮挡', () => {
  // 4 秒的比赛里 20 次切镜（间隔 20ms = 总时长 0.5%），
  // 旧实现生成 20 个几乎同位的按钮，实测点击被相邻按钮 intercept，一个都点不中。
  const base = Date.parse('2026-01-01T00:00:00Z')
  const events = [ev(0, new Date(base).toISOString(), 'match.created')]
  for (let i = 1; i <= 20; i += 1) {
    events.push(ev(i, new Date(base + i * 20).toISOString(), 'director.cue'))
  }
  events.push(ev(21, new Date(base + 4000).toISOString(), 'match.ended'))
  const tl = buildTimeline(events)
  const marks = marksOf(tl, events)
  assert.ok(marks.length < 20, `应合并重叠点，实际 ${marks.length}`)
  assert.ok(marks.length >= 2, '不能合并到只剩一个')
  assert.ok(marks.some((m) => m.type === 'match.ended'), '终局必须保留')
  for (let i = 1; i < marks.length; i += 1) {
    assert.ok(
      marks[i].t - marks[i - 1].t >= 0.012 || marks[i].type === 'match.ended',
      `第 ${i} 个点间距过小：${marks[i].t - marks[i - 1].t}`,
    )
  }
})

test('空流不抛异常', () => {
  const tl = buildTimeline([])
  assert.deepEqual(tl.points, [])
  assert.equal(cursorAt(tl, 0.5), 0)
  assert.equal(ratioAt(tl, 3), 0)
  assert.deepEqual(marksOf(tl, []), [])
})
