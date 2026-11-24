import test from 'node:test'
import assert from 'node:assert/strict'
import { createView, latestThoughtOf, reduce } from '../src/view.js'

const event = (seq, type, payload = {}, player = null) => ({ seq, type, payload, player, at: '2026-11-24T00:00:00Z' })

test('thought deltas accumulate into one readable thought within a turn', () => {
  let view = createView()
  view = reduce(view, event(0, 'match.created', { players: ['Atlas', 'Nova'], game: 'chess' }))
  view = reduce(view, event(1, 'turn.started', {}, 'Atlas'))
  view = reduce(view, event(2, 'thought.delta', { text: '我先看' }, 'Atlas'))
  view = reduce(view, event(3, 'thought.delta', { text: '中心的兵。' }, 'Atlas'))

  assert.equal(latestThoughtOf(view, 'Atlas'), '我先看中心的兵。')
  assert.deepEqual(view.thoughts.Atlas, ['我先看', '中心的兵。'])
})

test('a new turn resets the speaker thought but keeps the other player last turn', () => {
  let view = createView()
  view = reduce(view, event(0, 'match.created', { players: ['Atlas', 'Nova'], game: 'chess' }))
  view = reduce(view, event(1, 'turn.started', {}, 'Atlas'))
  view = reduce(view, event(2, 'thought.delta', { text: 'Atlas plan' }, 'Atlas'))
  view = reduce(view, event(3, 'turn.started', {}, 'Nova'))
  view = reduce(view, event(4, 'thought.delta', { text: 'Nova plan' }, 'Nova'))

  assert.equal(latestThoughtOf(view, 'Atlas'), 'Atlas plan')
  assert.equal(latestThoughtOf(view, 'Nova'), 'Nova plan')

  view = reduce(view, event(5, 'turn.started', {}, 'Nova'))
  assert.equal(latestThoughtOf(view, 'Nova'), '')
})
