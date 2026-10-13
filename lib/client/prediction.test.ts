import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { BattleResult } from '../battle/events.ts'
import type { Snapshot } from '../db/repo.ts'
import { winnerSlots } from './prediction.ts'

function snapshotWith(contestants: Array<{ id: string; slot: number }>): Snapshot {
  return {
    battle: {
      id: 'b1',
      arena: 'soft',
      topic: 't',
      status: 'completed',
      phase: 'completed',
      created_at: 1,
      completed_at: 2,
      error: null,
    },
    contestants: contestants.map((item) => ({
      id: item.id,
      battle_id: 'b1',
      slot: item.slot,
      provider_id: 'ollama',
      model_id: 'm',
      position_index: item.slot,
      prompt_tokens: null,
      completion_tokens: null,
      latency_ms: 10,
      output_text: '',
      code: null,
      status: 'ok',
      error_message: null,
    })),
    votes: [],
    predictions: [],
    judgements: [],
    sandboxResults: [],
  }
}

function resultWith(
  arena: BattleResult['arena'],
  verdicts: Array<{ slot: number; passed?: number; scores?: Record<string, number> }>,
): BattleResult {
  return {
    battleId: 'b1',
    arena,
    topic: 't',
    contestants: verdicts.map((verdict) => ({
      slot: verdict.slot,
      providerId: 'ollama',
      modelId: 'm',
      status: 'ok',
      usage: null,
      latencyMs: 100,
      outputLength: 10,
      code: null,
      errorMessage: null,
    })),
    verdicts: verdicts.map((verdict) => ({
      contestantSlot: verdict.slot,
      scores: verdict.scores ?? {},
      passed: verdict.passed ?? 0,
      total: 1,
      rationale: '',
    })),
    judgeAvailable: true,
  }
}

test('硬战场/残局：通过数最高者获胜；全员为零则无赢家', () => {
  const snapshot = snapshotWith([
    { id: 'c1', slot: 1 },
    { id: 'c2', slot: 2 },
  ])
  assert.deepEqual(
    winnerSlots('hard-code', resultWith('hard-code', [{ slot: 1, passed: 3 }, { slot: 2, passed: 1 }]), {}, snapshot),
    [1],
  )
  assert.deepEqual(
    winnerSlots('chess', resultWith('chess', [{ slot: 1, passed: 1 }, { slot: 2, passed: 0 }]), {}, snapshot),
    [1],
  )
  assert.deepEqual(
    winnerSlots('hard-code', resultWith('hard-code', [{ slot: 1, passed: 0 }, { slot: 2, passed: 0 }]), {}, snapshot),
    [],
  )
})

test('软战场系：先看人类投票，平票并列都算命中', () => {
  const snapshot = snapshotWith([
    { id: 'c1', slot: 1 },
    { id: 'c2', slot: 2 },
  ])
  const summary = resultWith('soft', [{ slot: 1, scores: { logic: 9 } }, { slot: 2, scores: { logic: 3 } }])
  assert.deepEqual(winnerSlots('soft', summary, { c2: 2, c1: 1 }, snapshot), [2])
  assert.deepEqual(winnerSlots('soft', summary, { c1: 1, c2: 1 }, snapshot), [1, 2])
})

test('软战场系：没有投票时退回裁判总分', () => {
  const snapshot = snapshotWith([
    { id: 'c1', slot: 1 },
    { id: 'c2', slot: 2 },
  ])
  const summary = resultWith('soft', [
    { slot: 1, scores: { logic: 5, evidence: 5 } },
    { slot: 2, scores: { logic: 8, evidence: 8 } },
  ])
  assert.deepEqual(winnerSlots('soft', summary, {}, snapshot), [2])
  assert.deepEqual(winnerSlots('soft', resultWith('soft', []), {}, snapshot), [])
  assert.deepEqual(winnerSlots('soft', null, { c1: 5 }, snapshot), [])
})
