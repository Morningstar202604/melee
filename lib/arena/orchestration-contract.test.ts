import assert from 'node:assert/strict'
import { test } from 'node:test'
import { getArena } from './index.ts'
import type { ArenaId } from './types.ts'

const ALL: ArenaId[] = ['soft', 'debate', 'banter', 'hard-code', 'chess']

test('编排契约：全部赛场都有输出预算帽（runaway 守卫）,且差异合理', () => {
  for (const id of ALL) {
    const budget = getArena(id).maxOutputTokens
    assert.equal(typeof budget, 'number', `${id} 缺 maxOutputTokens`)
    assert.ok((budget ?? 0) >= 64 && (budget ?? 0) <= 8000, `${id} 预算不合理：${budget}`)
  }
  assert.equal(getArena('chess').maxOutputTokens, 64, '残局只该输出一步棋')
  assert.ok((getArena('hard-code').maxOutputTokens ?? 0) >= (getArena('banter').maxOutputTokens ?? 0), '代码产出应比互怼有更大预算')
})

test('编排契约：判定来源与人数上限是显式声明的', () => {
  assert.equal(getArena('hard-code').judgeNeedsLlm, false, '沙箱是确定性判定')
  assert.equal(getArena('chess').judgeNeedsLlm, false, '棋规校验是确定性判定')
  assert.equal(getArena('soft').judgeNeedsLlm, undefined, '软战场需要 LLM 裁判')
  assert.equal(getArena('debate').judgeNeedsLlm, undefined)
  assert.equal(getArena('banter').judgeNeedsLlm, undefined)

  assert.equal(getArena('debate').maxContestants, 2, '互辩是 1v1')
  assert.equal(getArena('banter').maxContestants, 4, '互怼最多 4 人')
  for (const id of ALL) {
    const arena = getArena(id)
    assert.ok((arena.minContestants ?? 2) >= 2, `${id} 最少 2 人`)
    assert.ok((arena.maxContestants ?? 8) >= (arena.minContestants ?? 2), `${id} 人数区间应当有序`)
  }
})

test('编排契约：多轮赛场的回合数与超时预算是显式声明的', () => {
  for (const id of ALL) {
    const arena = getArena(id)
    if (arena.plan) {
      assert.ok(arena.plan.totalRounds >= 2, `${id} 多轮但回合数 < 2`)
      const perTurn = arena.turnTimeoutMs ?? 0
      const total = arena.battleTimeoutMs ?? 0
      assert.ok(perTurn > 0 && total > 0, `${id} 缺超时预算`)
      const worstCase = perTurn * arena.plan.totalRounds
      assert.ok(total >= worstCase, `${id} 全场预算 ${total} 覆盖不了最坏情况 ${worstCase}`)
    } else if (arena.turnTimeoutMs !== undefined) {
      assert.ok(arena.turnTimeoutMs > 0, `${id} 单轮超时必须为正数`)
    }
  }
})
