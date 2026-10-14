import { convertToModelMessages, createUIMessageStreamResponse, streamText, toUIMessageStream, type UIMessage } from 'ai'
import { getProvider, resolveModelRef, DEFAULT_MODEL_PROVIDER_ID } from '../../../lib/providers/registry.ts'
import { logger } from '../../../lib/log/logger.ts'
import { redact } from '../../../lib/log/redact.ts'

export const runtime = 'nodejs'

export const maxDuration = 60

const MAX_MESSAGES = 40
const MAX_MESSAGE_CHARS = 8000
const MAX_API_KEY_LENGTH = 500
const MAX_MODEL_ID_LENGTH = 200

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
  let body: ChatBody
  try {
    body = (await request.json()) as ChatBody
  } catch {
    return Response.json({ error: '请求体不是合法 JSON' }, { status: 400 })
  }

  const providerId = asString(body.providerId)
  const modelId = asString(body.modelId)
  const apiKey = typeof body.apiKey === 'string' ? body.apiKey.trim() : ''
  const messages = Array.isArray(body.messages) ? (body.messages as UIMessage[]) : []

  if (!providerId || !modelId || !apiKey) {
    return Response.json({ error: 'providerId / modelId / apiKey 必填' }, { status: 400 })
  }
  if (modelId.length > MAX_MODEL_ID_LENGTH || apiKey.length > MAX_API_KEY_LENGTH) {
    return Response.json({ error: 'modelId 或 apiKey 超长' }, { status: 400 })
  }
  const provider = getProvider(providerId)
  if (!provider) {
    return Response.json({ error: `未知 provider：${providerId}` }, { status: 400 })
  }
  if (messages.length === 0 || messages.length > MAX_MESSAGES) {
    return Response.json({ error: `messages 数量需在 1~${MAX_MESSAGES}` }, { status: 400 })
  }
  for (const message of messages) {
    const text = JSON.stringify(message?.parts ?? '')
    if (text.length > MAX_MESSAGE_CHARS) {
      return Response.json({ error: '单条消息超长' }, { status: 400 })
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
    return createUIMessageStreamResponse({
      stream: toUIMessageStream({
        stream: result.stream,
        sendReasoning: true,
        // 把底层错误转成可读文本（默认是 "An error occurred."）
        onError: (error) =>
          redact(error instanceof Error ? error.message : String(error), [apiKey]),
      }),
    })
  } catch (error) {
    const message = redact(error instanceof Error ? error.message : String(error), [apiKey])
    logger.error('单挑台调用失败', { error: message })
    return Response.json({ error: `调用失败：${message}` }, { status: 500 })
  }
}
