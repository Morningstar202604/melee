import { convertToModelMessages, createUIMessageStreamResponse, streamText, toUIMessageStream, type UIMessage } from 'ai'
import { getProvider, resolveModelRef, DEFAULT_MODEL_PROVIDER_ID } from '../../../lib/providers/registry.ts'
import { logger } from '../../../lib/log/logger.ts'
import { redact } from '../../../lib/log/redact.ts'
import { chatLimiter } from '../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse, readJsonCapped } from '../../../lib/http/request.ts'
import { hardenResponse, secureResponse } from '../../../lib/security.ts'

export const runtime = 'nodejs'

export const maxDuration = 60

const MAX_MESSAGES = 40
const MAX_MESSAGE_CHARS = 8000
const MAX_API_KEY_LENGTH = 500
const MAX_MODEL_ID_LENGTH = 200
// 40 条 × 8000 字符的 CJK 每字符 3 字节 ≈ 960KB；emoji 场景按 UTF-16/UTF-8
// 膨胀也不会到 2MB 的一半。上限要 ≥ 各字段上限的字节和，否则合法请求会被 413。
const MAX_BODY_BYTES = 2 * 1024 * 1024

interface ChatBody {
  messages?: unknown
  providerId?: unknown
  modelId?: unknown
  apiKey?: unknown
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export async function POST(request: Request) {
  const ip = clientIp(request)
  if (!chatLimiter.allow(ip)) {
    logger.warn('聊天限流拒绝', { ip })
    return rateLimitedResponse(chatLimiter, '轮对话')
  }

  const read = await readJsonCapped<ChatBody>(request, MAX_BODY_BYTES)
  if (!read.ok) return read.response
  const body = read.body ?? {}

  const providerId = asString(body.providerId)
  const modelId = asString(body.modelId)
  const apiKey = typeof body.apiKey === 'string' ? body.apiKey.trim() : ''
  const messages = Array.isArray(body.messages) ? (body.messages as UIMessage[]) : []

  if (!providerId || !modelId || !apiKey) {
    return secureResponse(JSON.stringify({ error: 'providerId / modelId / apiKey 必填' }), {
      status: 400,
    })
  }
  if (modelId.length > MAX_MODEL_ID_LENGTH || apiKey.length > MAX_API_KEY_LENGTH) {
    return secureResponse(JSON.stringify({ error: 'modelId 或 apiKey 超长' }), { status: 400 })
  }
  const provider = getProvider(providerId)
  if (!provider) {
    return secureResponse(JSON.stringify({ error: `未知 provider：${providerId}` }), { status: 400 })
  }
  if (messages.length === 0 || messages.length > MAX_MESSAGES) {
    return secureResponse(JSON.stringify({ error: `messages 数量需在 1~${MAX_MESSAGES}` }), {
      status: 400,
    })
  }
  for (const message of messages) {
    const text = JSON.stringify(message?.parts ?? '')
    if (text.length > MAX_MESSAGE_CHARS) {
      return secureResponse(JSON.stringify({ error: '单条消息超长' }), { status: 400 })
    }
  }

  const modelRef = providerId === DEFAULT_MODEL_PROVIDER_ID ? modelId : `${providerId}:${modelId}`

  try {
    const result = streamText({
      model: resolveModelRef(modelRef, apiKey),
      messages: await convertToModelMessages(messages),
      system:
        '你是「AI 大乱斗」单挑台的选手，直接回答用户的问题。中文作答，简洁、有观点、有幽默感；不要寒暄。',
      maxRetries: 2,
      maxOutputTokens: 2000,
      onError: ({ error }) => {
        logger.debug('单挑台流式错误', {
          detail: redact(error instanceof Error ? error.message : String(error), [apiKey]),
        })
      },
    })
    // 官方推荐组合（toUIMessageStreamResponse 已标记 deprecated）
    return hardenResponse(
      createUIMessageStreamResponse({
        stream: toUIMessageStream({
          stream: result.stream,
          sendReasoning: true,
          // 把底层错误转成可读文本（默认是 "An error occurred."）
          onError: (error) =>
            redact(error instanceof Error ? error.message : String(error), [apiKey]),
        }),
      }),
    )
  } catch (error) {
    const message = redact(error instanceof Error ? error.message : String(error), [apiKey])
    logger.error('单挑台调用失败', { error: message })
    return secureResponse(JSON.stringify({ error: `调用失败：${message}` }), { status: 500 })
  }
}
