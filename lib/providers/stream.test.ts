import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { createOpenAI } from '@ai-sdk/openai'
import type { LanguageModel } from 'ai'
import { startFakeServer, type FakeOpenAIServer } from '../../test/fixtures/fake-openai-server.ts'
import { streamModel, type StreamEvent } from './stream.ts'

let server: FakeOpenAIServer

before(async () => {
  server = await startFakeServer()
})

after(async () => {
  await server.close()
})

function makeModel(baseUrl = server.baseUrl): LanguageModel {
  return createOpenAI({ baseURL: baseUrl, apiKey: 'test-key' }).chat('fake-model')
}

async function collect(options?: { apiKey?: string; signal?: AbortSignal }): Promise<StreamEvent[]> {
  const events: StreamEvent[] = []
  for await (const event of streamModel(makeModel(), [{ role: 'user', content: '你好' }], {
    signal: options?.signal ?? AbortSignal.timeout(15000),
    apiKey: options?.apiKey,
    providerLabel: 'DeepSeek',
  })) {
    events.push(event)
  }
  return events
}

test('streamModel 依次收到 你 / 好 两个 delta 再收到 done', async () => {
  const events = await collect()
  const types = events.map((e) => e.type)
  assert.deepEqual(types, ['delta', 'delta', 'done'])

  const text = events.map((e) => (e.type === 'delta' ? e.text : '')).join('')
  assert.equal(text, '你好')

  const done = events.at(-1)
  assert.equal(done?.type, 'done')
  if (done?.type !== 'done') return
  assert.deepEqual(done.usage, { prompt: 7, completion: 2 })
  assert.ok(done.latencyMs >= 0)
  assert.equal(server.paths.at(-1), '/chat/completions')
})

test('streamModel 走的是 /chat/completions，不是 responses', async () => {
  const before = server.paths.length
  await collect()
  const usedPaths = server.paths.slice(before)
  assert.ok(usedPaths.includes('/chat/completions'))
  assert.ok(!usedPaths.some((p) => p.includes('/responses')))
})

test('HTTP 401 映射为 auth_failed，消息带 provider 名且不泄露 Key', async () => {
  server.setStatus(401)
  try {
    const events = await collect({ apiKey: 'sk-super-secret-value' })
    const last = events.at(-1)
    assert.equal(last?.type, 'error')
    if (last?.type !== 'error') return
    assert.equal(last.status, 'auth_failed')
    assert.ok(last.message.includes('DeepSeek'), last.message)
    assert.ok(!last.message.includes('sk-super-secret-value'), last.message)
    assert.ok(!last.message.includes('Bearer'), last.message)
  } finally {
    server.setStatus(null)
  }
})

test('HTTP 5xx 的错误详情里的 Key 被脱敏', async () => {
  server.setStatus(503)
  try {
    const events = await collect({ apiKey: 'sk-super-secret-value' })
    const last = events.at(-1)
    assert.equal(last?.type, 'error')
    if (last?.type !== 'error') return
    assert.equal(last.status, 'provider_error')
    assert.ok(!last.message.includes('sk-super-secret-value'), last.message)
    assert.ok(last.message.includes('***'), last.message)
  } finally {
    server.setStatus(null)
  }
})

test('HTTP 429 映射为 rate_limited', async () => {
  server.setStatus(429)
  try {
    const events = await collect()
    const last = events.at(-1)
    assert.equal(last?.type, 'error')
    if (last?.type !== 'error') return
    assert.equal(last.status, 'rate_limited')
    assert.ok(last.message.includes('DeepSeek'))
  } finally {
    server.setStatus(null)
  }
})

test('HTTP 502 映射为 provider_error', async () => {
  server.setStatus(502)
  try {
    const events = await collect()
    const last = events.at(-1)
    assert.equal(last?.type, 'error')
    if (last?.type !== 'error') return
    assert.equal(last.status, 'provider_error')
    assert.ok(last.message.includes('HTTP 502'))
  } finally {
    server.setStatus(null)
  }
})

test('abort 映射为 timeout', async () => {
  const controller = new AbortController()
  setTimeout(() => controller.abort(), 1)
  const events = await collect({ signal: controller.signal })
  const last = events.at(-1)
  assert.equal(last?.type, 'error')
  if (last?.type !== 'error') return
  assert.equal(last.status, 'timeout')
})

test('空输出映射为 empty 且不发 done', async () => {
  const emptyServer = await startFakeServer({ chunks: [], latencyMs: 1 })
  try {
    const events: StreamEvent[] = []
    for await (const event of streamModel(makeModel(emptyServer.baseUrl), [{ role: 'user', content: 'hi' }], {
      signal: AbortSignal.timeout(15000),
      providerLabel: 'SiliconFlow',
    })) {
      events.push(event)
    }
    const types = events.map((e) => e.type)
    assert.deepEqual(types, ['error'])
    const last = events.at(-1)
    if (last?.type !== 'error') return
    assert.equal(last.status, 'empty')
    assert.ok(last.message.includes('未返回任何内容'))
  } finally {
    await emptyServer.close()
  }
})

test('不可达端点映射为 provider_error 且不 500 / 不抛到调用方', async () => {
  const events: StreamEvent[] = []
  for await (const event of streamModel(makeModel('http://127.0.0.1:1/v1'), [{ role: 'user', content: 'hi' }], {
    signal: AbortSignal.timeout(20000),
    providerLabel: 'Ollama（本地）',
  })) {
    events.push(event)
  }
  const last = events.at(-1)
  assert.equal(last?.type, 'error')
  if (last?.type !== 'error') return
  assert.equal(last.status, 'provider_error')
  assert.ok(last.message.includes('Ollama（本地）'))
})
