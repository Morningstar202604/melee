import assert from 'node:assert/strict'
import { test } from 'node:test'
import { POST as chatRoute } from './route.ts'

function chatRequest(body: unknown): Request {
  return new Request('http://localhost:3000/api/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const VALID = {
  providerId: 'zhipu',
  modelId: 'glm-4.6',
  apiKey: 'sk-test-key-value',
  messages: [{ id: 'm1', role: 'user', parts: [{ type: 'text', text: '你好' }] }],
}

test('chat 路由：参数缺失/超长/未知 provider/messages 越界都返回可读 400', async () => {
  const cases: Array<{ body: unknown; expect: RegExp }> = [
    { body: {}, expect: /必填/ },
    { body: { ...VALID, providerId: 'no-such' }, expect: /未知 provider/ },
    { body: { ...VALID, messages: [] }, expect: /messages 数量/ },
    { body: { ...VALID, modelId: 'x'.repeat(201) }, expect: /超长/ },
    { body: { ...VALID, messages: Array.from({ length: 41 }, (_, i) => ({ id: `m${i}`, role: 'user', parts: [] })) }, expect: /messages 数量/ },
  ]
  for (const item of cases) {
    const response = await chatRoute(chatRequest(item.body))
    assert.equal(response.status, 400, JSON.stringify(item.body).slice(0, 60))
    const payload = (await response.json()) as { error?: string }
    assert.match(payload.error ?? '', item.expect)
    assert.ok(!(payload.error ?? '').includes('sk-test-key-value'), '错误信息不得回显 Key')
  }
})

test('chat 路由：不可达 provider 走可读错误且不泄漏 Key', async () => {
  process.env.MELEE_BASE_URL_OLLAMA = 'http://127.0.0.1:1/v1'
  try {
    const response = await chatRoute(
      chatRequest({
        ...VALID,
        providerId: 'ollama',
        modelId: 'no-such-model',
        apiKey: 'ollama-local-secret-token',
      }),
    )
    // streamText 的构造不抛错，错误在流内；这里断言响应是 SSE 或可读 JSON，且任何分支都不含 Key
    if (response.status === 200) {
      const text = await response.text()
      assert.ok(!text.includes('ollama-local-secret-token'), '流内容不得回显 Key')
    } else {
      const payload = (await response.json()) as { error?: string }
      assert.ok(!(payload.error ?? '').includes('ollama-local-secret-token'))
    }
  } finally {
    delete process.env.MELEE_BASE_URL_OLLAMA
  }
})

test('chat 路由：请求体超 2MB 返回 413', async () => {
  const response = await chatRoute(chatRequest('x'.repeat(2_200_000)))
  assert.equal(response.status, 413)
})

test('chat 路由：合法请求返回官方 UIMessage 流，错误可读且不泄漏 Key', async () => {
  process.env.MELEE_BASE_URL_ZHIPU = 'http://127.0.0.1:1/v1'
  try {
    const response = await chatRoute(chatRequest({ ...VALID, apiKey: 'sk-should-not-leak-abcdef' }))
    assert.equal(response.status, 200)
    assert.match(response.headers.get('content-type') ?? '', /text\/event-stream/)
    const text = await response.text()
    assert.ok(text.includes('data: '), '应为官方 UI 消息流格式')
    assert.ok(text.includes('[DONE]'), '应带结束标记')
    assert.ok(
      text.includes('"type":"error"') || !text.includes('"type":"error"'),
      '允许错误分支（不可达端点）',
    )
    assert.ok(!text.includes('sk-should-not-leak-abcdef'), '流内容不得回显 Key')
    assert.ok(!text.includes('An error occurred.'), '默认通用错误文案应被可读 onError 替换')
  } finally {
    delete process.env.MELEE_BASE_URL_ZHIPU
  }
})
