import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_MODEL_PROVIDER_ID,
  PROVIDERS,
  createModel,
  getProvider,
  listModels,
  parseCustomProviders,
  resolveModelRef,
} from './registry.ts'
import { startFakeServer, type FakeOpenAIServer } from '../../test/fixtures/fake-openai-server.ts'

const CN_IDS = ['ollama', 'deepseek', 'siliconflow', 'zhipu', 'moonshot', 'dashscope']
const PROXY_IDS = ['openai', 'anthropic', 'google']

const CN_KINDS: Record<string, string> = {
  ollama: 'openai-compatible',
  deepseek: 'deepseek',
  siliconflow: 'openai-compatible',
  zhipu: 'openai-compatible',
  moonshot: 'moonshot',
  dashscope: 'alibaba',
}

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
    assert.equal(p.kind, CN_KINDS[id], id)
    assert.equal(p.region, 'cn', id)
    assert.equal(p.needsProxy, false, id)
  }

  for (const id of PROXY_IDS) {
    const p = byId.get(id)
    assert.ok(p, `缺少 provider ${id}`)
    assert.equal(p.needsProxy, true, id)
  }

  const openai = byId.get('openai')
  assert.equal(openai?.kind, 'openai', 'OpenAI 走官方 openai 包')

  const gateway = byId.get('gateway')
  assert.ok(gateway, '缺少官方 AI Gateway provider')
  assert.equal(gateway.kind, 'gateway')
  assert.equal(gateway.needsProxy, false)
})

test('PROVIDERS 的 id 唯一', () => {
  const ids = PROVIDERS.map((p) => p.id)
  assert.equal(new Set(ids).size, ids.length)
})

test('除无端点型 provider 外都必须带 http baseUrl（列表用）', () => {
  for (const p of PROVIDERS) {
    if (p.kind === 'anthropic' || p.kind === 'google' || p.kind === 'gateway') {
      assert.equal(p.baseUrl, undefined, p.id)
    } else {
      assert.ok(p.baseUrl?.startsWith('http'), `${p.id} 缺少 baseUrl`)
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

test('createModel 一律返回 chat 模型（官方 openai-compatible 协议），不是 responses', () => {
  const zhipu = getProvider('zhipu')
  assert.ok(zhipu)
  const model = createModel({ ...zhipu, baseUrl: server.baseUrl }, 'fake-model', 'test-key')
  assert.ok(
    (model as { provider: string }).provider.endsWith('.chat'),
    (model as { provider: string }).provider,
  )
  assert.ok(!(model as { provider: string }).provider.includes('responses'))
})

test('resolveModelRef：provider:model 前缀走官方注册表寻址，无前缀落到默认 provider', async () => {
  const zhipu = getProvider('zhipu')!
  // 直接用官方地址解析（不发起网络请求，只构造模型对象）
  const prefixed = resolveModelRef(`${zhipu.id}:fake-model`, 'test-key')
  assert.ok(prefixed, '带前缀引用应当解析成功')
  const bare = resolveModelRef('fake-model', 'test-key')
  assert.ok(bare, `无前缀应落到默认 provider ${DEFAULT_MODEL_PROVIDER_ID}`)
  assert.throws(() => resolveModelRef('no-such-provider:model', 'k'), /未知 provider 前缀/)
})

test('parseCustomProviders：合法自定义厂家注入、非法条目跳过', () => {
  const parsed = parseCustomProviders(
    JSON.stringify([
      { id: 'vllm-local', label: '本地 vLLM', baseUrl: 'http://127.0.0.1:8000/v1' },
      { id: 'bad id!', label: 'x', baseUrl: 'http://x' },
      { id: 'no-url', label: 'x' },
      { id: 'ok-global', label: '海外中转', baseUrl: 'https://relay.example.com/v1', region: 'global' },
    ]),
  )
  assert.deepEqual(
    parsed.map((p) => p.id),
    ['vllm-local', 'ok-global'],
  )
  assert.equal(parsed[1]?.region, 'global')
  assert.ok(parsed.every((p) => p.kind === 'openai-compatible' && p.needsProxy === false))
  assert.deepEqual(parseCustomProviders('not-json'), [])
  assert.deepEqual(parseCustomProviders(undefined), [])
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
