import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { PROVIDERS, createModel, getProvider, listModels } from './registry.ts'
import { startFakeServer, type FakeOpenAIServer } from '../../test/fixtures/fake-openai-server.ts'

const CN_IDS = ['ollama', 'deepseek', 'siliconflow', 'zhipu', 'moonshot', 'dashscope']
const PROXY_IDS = ['openai', 'anthropic', 'google']

let server: FakeOpenAIServer

before(async () => {
  server = await startFakeServer()
})

after(async () => {
  await server.close()
})

test('PROVIDERS 覆盖国内可直连与需代理两组', () => {
  const byId = new Map(PROVIDERS.map((p) => [p.id, p]))
  assert.equal(byId.size, PROVIDERS.length)

  for (const id of CN_IDS) {
    const p = byId.get(id)
    assert.ok(p, `缺少 provider ${id}`)
    assert.equal(p.kind, 'openai-compatible', id)
    assert.equal(p.region, 'cn', id)
    assert.equal(p.needsProxy, false, id)
  }

  for (const id of PROXY_IDS) {
    const p = byId.get(id)
    assert.ok(p, `缺少 provider ${id}`)
    assert.equal(p.needsProxy, true, id)
  }
})

test('PROVIDERS 的 id 唯一，且不预填任何 model id', () => {
  const ids = PROVIDERS.map((p) => p.id)
  assert.equal(new Set(ids).size, ids.length)
  assert.ok(
    PROVIDERS.every((p) => p.defaultModelId === undefined),
    'defaultModelId 必须留空，由 UI 手填或 listModels 选',
  )
})

test('openai-compatible 必须带 http baseUrl，anthropic/google 不带', () => {
  for (const p of PROVIDERS) {
    if (p.kind === 'openai-compatible') {
      assert.ok(p.baseUrl?.startsWith('http'), `${p.id} 缺少 baseUrl`)
    } else {
      assert.equal(p.baseUrl, undefined, p.id)
    }
  }
})

test('createModel 对所有 provider 都不抛错', () => {
  for (const p of PROVIDERS) {
    const model = createModel(p, 'any-model-id', 'test-key')
    assert.ok(model, p.id)
    const concrete = model as { doStream: unknown; provider: string; modelId: string }
    assert.equal(typeof concrete.doStream, 'function', p.id)
    assert.equal(typeof concrete.provider, 'string', p.id)
    assert.equal(concrete.modelId, 'any-model-id', p.id)
  }
})

test('createModel 对 openai-compatible 返回 chat 模型而非 responses 模型', () => {
  const deepseek = getProvider('deepseek')
  assert.ok(deepseek)
  const model = createModel({ ...deepseek, baseUrl: server.baseUrl }, 'fake-model', 'test-key')
  assert.equal((model as { provider: string }).provider, 'openai.chat')
})

test('getProvider 未知 id 返回 undefined', () => {
  assert.equal(getProvider('no-such-provider'), undefined)
})

test('listModels 拉取并排序 openai-compatible 端点模型', async () => {
  const deepseek = getProvider('deepseek')
  assert.ok(deepseek)
  const result = await listModels({ ...deepseek, baseUrl: server.baseUrl }, 'test-key')
  assert.equal(result.ok, true)
  assert.deepEqual(result.models, ['fake-model-a', 'fake-model-b'])
})

test('listModels 对非 openai-compatible 直接要求手填', async () => {
  const result = await listModels(getProvider('anthropic')!, 'test-key')
  assert.equal(result.ok, false)
  assert.equal(result.reason, '该 provider 需手填模型 id')
})

test('listModels 不可达时返回可读原因且不含 Key', async () => {
  const ollama = getProvider('ollama')
  assert.ok(ollama)
  const result = await listModels({ ...ollama, baseUrl: 'http://127.0.0.1:1/v1' }, 'sk-secret-value')
  assert.equal(result.ok, false)
  assert.ok(result.reason && result.reason.length > 0)
  assert.ok(!result.reason!.includes('sk-secret-value'))
})
