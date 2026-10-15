import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  battleLimiter,
  chatLimiter,
  probeLimiter,
  spectatorLimiter,
  rateLimitMessage,
} from './ratelimit.ts'

test('分档上限互相独立：预检打满不影响聊天额度', () => {
  const ip = `ip-independent-${Math.random().toString(36).slice(2)}`
  for (let i = 0; i < probeLimiter.profile.maxPerWindow; i += 1) {
    assert.equal(probeLimiter.allow(ip), true, `第 ${i + 1} 次预检应放行`)
  }
  assert.equal(probeLimiter.allow(ip), false, '超出预检上限必须拒绝')
  assert.equal(chatLimiter.allow(ip), true, '预检用尽不得消耗聊天额度（分档独立）')
  assert.equal(spectatorLimiter.allow(ip), true, '预检用尽不得消耗观众额度')
})

test('建房档位复用历史入口语义：第 6 次拒绝且进入封禁；状态可查询', () => {
  const ip = `ip-battle-${Math.random().toString(36).slice(2)}`
  for (let i = 0; i < battleLimiter.profile.maxPerWindow; i += 1) {
    assert.equal(battleLimiter.allow(ip), true)
  }
  assert.equal(battleLimiter.allow(ip), false)
  const status = battleLimiter.status(ip)
  assert.equal(status.maxPerWindow, 5)
  assert.equal(status.blocked, false, '第一次超限只触发计数，连续 3 次才封禁')
})

test('10 秒内被拒 3 次进入封禁名单', () => {
  const ip = `ip-block-${Math.random().toString(36).slice(2)}`
  for (let i = 0; i < battleLimiter.profile.maxPerWindow; i += 1) {
    battleLimiter.allow(ip)
  }
  // 连续三次被拒 → 第 3 次触发封禁
  assert.equal(battleLimiter.allow(ip), false)
  assert.equal(battleLimiter.allow(ip), false)
  assert.equal(battleLimiter.allow(ip), false)
  const status = battleLimiter.status(ip)
  assert.equal(status.blocked, true, '连续 3 次被拒后应进入封禁')
  assert.ok(status.blockExpiresInMs > 0)
})

test('429 文案带档位参数（窗口秒数与上限）', () => {
  const chatMessage = rateLimitMessage(chatLimiter.profile, '轮对话')
  assert.ok(chatMessage.includes('60 秒'))
  assert.ok(chatMessage.includes(String(chatLimiter.profile.maxPerWindow)))
  assert.ok(chatMessage.includes('轮对话'))
  const battleMessage = rateLimitMessage(battleLimiter.profile, '场对战')
  assert.ok(battleMessage.includes('5'))
  assert.ok(battleMessage.includes('场对战'))
})

test('不同 IP 互不影响', () => {
  const a = `ip-a-${Math.random().toString(36).slice(2)}`
  const b = `ip-b-${Math.random().toString(36).slice(2)}`
  for (let i = 0; i < chatLimiter.profile.maxPerWindow; i += 1) {
    assert.equal(chatLimiter.allow(a), true)
  }
  assert.equal(chatLimiter.allow(a), false)
  assert.equal(chatLimiter.allow(b), true, '其他 IP 不受影响')
})
