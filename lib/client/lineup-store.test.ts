import assert from 'node:assert/strict'
import { before, test } from 'node:test'

/** 最小 localStorage/sessionStorage mock（node 环境无 window） */
function makeStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => [...map.keys()][index] ?? null,
    removeItem: (key: string) => {
      map.delete(key)
    },
    setItem: (key: string, value: string) => {
      map.set(key, String(value))
    },
  } as Storage
}

const localStorage = makeStorage()
const sessionStorage = makeStorage()

let migrateLegacyLineup: () => Record<string, string>
let useLineupStore: typeof import('./lineup-store.ts').useLineupStore

before(async () => {
  ;(globalThis as Record<string, unknown>).window = { localStorage, sessionStorage }
  const mod = await import('./lineup-store.ts')
  migrateLegacyLineup = mod.migrateLegacyLineup
  useLineupStore = mod.useLineupStore
})

test('迁移：旧格式并入新 store、Key 只来自 sessionStorage、旧 key 被清理', () => {
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem(
    'melee.lineup',
    JSON.stringify({
      arena: 'banter',
      topicByArena: { soft: 'soft-debate-lieflat', banter: 'banter-blame' },
      rows: [
        { rowId: 'r1', providerId: 'deepseek', modelId: 'deepseek-chat' },
        { rowId: 'r2', providerId: 'moonshot', modelId: 'kimi-k2-0905' },
      ],
    }),
  )
  sessionStorage.setItem('melee.keys', JSON.stringify({ r1: 'sk-old-key', r2: '' }))

  const keys = migrateLegacyLineup()

  const state = useLineupStore.getState()
  assert.equal(state.arena, 'banter')
  assert.equal(state.topicByArena.banter, 'banter-blame')
  assert.equal(state.rows.length, 2)
  assert.equal(state.rows[0]?.providerId, 'deepseek')
  assert.deepEqual(keys, { r1: 'sk-old-key' }, '只有非空 Key 被返回')
  assert.equal(localStorage.getItem('melee.lineup'), null, '旧阵容 key 应被清理')
})

test('Key 绝不进入 localStorage：persist 只序列化阵容字段', async () => {
  await new Promise((resolve) => setTimeout(resolve, 20))
  const persisted = localStorage.getItem('melee.lineup.v2')
  assert.ok(persisted, 'persist 中间件应写入 v2 key')
  assert.ok(!persisted!.includes('sk-'), '持久化内容不得包含任何 Key')
  const parsed = JSON.parse(persisted!) as { state?: { rows?: unknown[] } }
  assert.ok(Array.isArray(parsed.state?.rows))
})

test('无旧数据时迁移幂等：不覆盖现有 store，仅回灌 session keys', () => {
  localStorage.clear()
  sessionStorage.clear()
  useLineupStore.setState({
    arena: 'chess',
    topicByArena: { chess: 'chess-hunt' },
    rows: [{ rowId: 'x1', providerId: 'zhipu', modelId: 'glm-4.6' }],
  })
  sessionStorage.setItem('melee.keys', JSON.stringify({ x1: 'sk-session-only' }))
  const keys = migrateLegacyLineup()
  assert.deepEqual(keys, { x1: 'sk-session-only' })
  assert.equal(useLineupStore.getState().arena, 'chess', '现有 store 不应被清空/覆盖')
})

test('setRows 更新被持久化（异步写出）', async () => {
  useLineupStore.getState().setRows((rows) => [...rows, { rowId: 'x2', providerId: 'openai', modelId: 'gpt-5-mini' }])
  await new Promise((resolve) => setTimeout(resolve, 20))
  const persisted = localStorage.getItem('melee.lineup.v2')
  assert.ok(persisted?.includes('gpt-5-mini'), '新增行应写入持久化')
})
