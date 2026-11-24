import test from 'node:test'
import assert from 'node:assert/strict'
import { appendTrace, formatAction, isTraceEvent, makeTrace, traceText } from '../src/trace.js'

const event = (seq, type, payload = {}, player = null) => ({ seq, type, payload, player })

test('trace keeps actions, narration and delivered coach commands, not token noise', () => {
  const events = [
    event(1, 'thought.delta', { text: 'hidden' }, 'A'),
    event(2, 'coach.message', { commandId: 'c1', target: 'A', instruction: 'go', status: 'queued' }),
    event(3, 'coach.message', { commandId: 'c1', target: 'A', instruction: 'go', status: 'delivered' }),
    event(4, 'state.changed', {}),
    event(5, 'action.applied', { action: { kind: 'guess', value: 'e4' } }, 'A'),
  ]
  assert.equal(isTraceEvent(events[0]), false)
  assert.equal(isTraceEvent(events[1]), false)
  assert.equal(isTraceEvent(events[2]), true)
  assert.deepEqual(makeTrace(events).map((item) => item.seq), [3, 5])
})

test('appendTrace only grows on relevant events and caps the window', () => {
  const trace = []
  assert.equal(appendTrace(trace, event(1, 'thought.delta', {})), trace)
  const first = appendTrace(trace, event(2, 'action.proposed', {}))
  assert.equal(first.length, 1)
  let capped = first
  for (let seq = 3; seq < 30; seq += 1) capped = appendTrace(capped, event(seq, 'action.applied', {}), 5)
  assert.deepEqual(capped.map((item) => item.seq), [25, 26, 27, 28, 29])
})

test('action formatting never leaks an object into the timeline', () => {
  assert.equal(formatAction({ word: 'center', number: 2 }), 'word: center · number: 2')
  assert.equal(formatAction(['e4', null, 'Nf3']), 'e4 · Nf3')
  const text = traceText(
    event(7, 'action.proposed', { action: { kind: 'guess', value: { x: 1, y: 2 } } }, 'Atlas'),
    (key) => ({ 'arena3d.turn': 'turn', 'watch.status.ended': 'ended' }[key] || key),
  )
  assert.equal(text, 'Atlas · x: 1 · y: 2')
  assert.ok(!text.includes('[object Object]'))
})

test('trace text uses the page language for turn and end states', () => {
  const zh = (key) => ({ 'arena3d.turn': '回合', 'arena3d.turnStart': '回合开始', 'watch.status.ended': '已结束' }[key] || key)
  assert.equal(traceText(event(10, 'turn.started', { turnIndex: 4 }, 'Nova'), zh), 'Nova · 回合 4')
  assert.equal(traceText(event(11, 'turn.started', {}, 'Nova'), zh), 'Nova · 回合开始')
  assert.equal(traceText(event(12, 'match.ended', { winner: 'Nova', reason: 'line' }), zh), '已结束 · Nova')
})
