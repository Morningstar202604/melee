// 教练事件归并：queued 与 delivered 是同一条指令的两个状态，不能显示成两条。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createView, reduce } from '../src/view.js'

const event = (seq, status, id = 1, target = 'A', instruction = '先占中心') => ({
  seq,
  type: 'coach.message',
  payload: { commandId: id, target, instruction, status },
})

test('queued → delivered 合并为同一条，状态取最新', () => {
  const view = reduce(createView(), event(1, 'queued'))
  assert.equal(view.coach.length, 1)
  const applied = reduce(view, event(2, 'delivered'))
  assert.equal(applied.coach.length, 1)
  assert.equal(applied.coach[0].status, 'delivered')
  assert.equal(applied.lastCoach.id, 1)
})

test('不同 commandId 分开保留，最多 8 条', () => {
  let view = createView()
  for (let id = 1; id <= 10; id += 1) {
    view = reduce(view, event(id, 'queued', id, 'A', `指令 ${id}`))
  }
  assert.equal(view.coach.length, 8)
  assert.equal(view.coach[0].id, 3)
  assert.equal(view.coach.at(-1).id, 10)
  assert.equal(view.lastCoach.instruction, '指令 10')
})
