import { createUIMessageStream } from 'ai'
import {
  closesStream,
  getBattleHandle,
  unregisterBattle,
  type BattleEventListener,
} from '../../../../../lib/battle/registry.ts'
import type { BattleEvent } from '../../../../../lib/battle/events.ts'
import { BATTLE_DATA_PART, type BattleUIMessage } from '../../../../../lib/battle/ui-stream.ts'

export const runtime = 'nodejs'

const HEARTBEAT_MS = 15000
const SETTLE_GRACE_MS = 60000

const HEARTBEAT_COMMENT = ':ping\n'

function unref(timer: NodeJS.Timeout): void {
  timer.unref()
}

export async function GET(
  request: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params
  const handle = getBattleHandle(id)
  if (!handle) {
    return Response.json(
      { error: '对战不存在或已回收，请从 /api/battle/' + id + '/snapshot 获取战报' },
      { status: 404 },
    )
  }

  const lastIdHeader = request.headers.get('last-event-id')
  const parsedLastId = lastIdHeader === null ? -1 : Number(lastIdHeader)
  const lastId = Number.isFinite(parsedLastId) ? Math.trunc(parsedLastId) : -1

  // 事件生产走官方 createUIMessageStream（数据部件协议）；验证过 N 个并发生产者保序写入。
  // 首个 SSE id 与事件日志对齐：handle.nextId() - 将重放的历史条数。
  let seq = 0
  const uiStream = createUIMessageStream<BattleUIMessage>({
    execute: ({ writer }) =>
      new Promise<void>((resolve) => {
        let closed = false
        let unsubscribe: () => void = () => {}

        const close = () => {
          if (closed) return
          closed = true
          unsubscribe()
          unsubscribe = () => {}
          resolve()
        }

        const writeEvent = (event: BattleEvent) => {
          if (closed) return
          writer.write({ type: BATTLE_DATA_PART, data: event })
          if (closesStream(event)) {
            unref(setTimeout(() => unregisterBattle(id), SETTLE_GRACE_MS))
            close()
          }
        }

        const backlog = handle.eventsSince(lastId)
        seq = handle.nextId() - backlog.length

        let replayDone = false
        const pending: BattleEvent[] = []
        const listener: BattleEventListener = (event) => {
          if (!replayDone) {
            pending.push(event)
            return
          }
          writeEvent(event)
        }
        unsubscribe = handle.subscribe(listener)

        for (const event of backlog) {
          if (closed) break
          writeEvent(event)
        }
        replayDone = true
        for (const event of pending) {
          if (closed) break
          writeEvent(event)
        }

        request.signal?.addEventListener('abort', close)
      }),
  })

  const encoder = new TextEncoder()
  const responseStream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let closed = false
      const finish = () => {
        if (closed) return
        closed = true
        if (heartbeat) clearInterval(heartbeat)
        try {
          controller.close()
        } catch {}
      }
      const heartbeat = setInterval(() => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(HEARTBEAT_COMMENT))
        } catch {
          finish()
        }
      }, HEARTBEAT_MS)
      request.signal?.addEventListener('abort', finish)

      const reader = uiStream.getReader()
      try {
        for (;;) {
          const { value, done } = await reader.read()
          if (done || closed) break
          if (value.type !== BATTLE_DATA_PART) continue
          controller.enqueue(encoder.encode(`id: ${seq}\ndata: ${JSON.stringify(value)}\n\n`))
          seq += 1
        }
      } catch {
        // 连接侧异常：按关闭处理
      }
      finish()
    },
  })

  return new Response(responseStream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
