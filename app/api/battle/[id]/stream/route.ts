import {
  closesStream,
  getBattleHandle,
  unregisterBattle,
  type BattleEventListener,
} from '../../../../../lib/battle/registry.ts'
import type { BattleEvent } from '../../../../../lib/battle/events.ts'

export const runtime = 'nodejs'

const HEARTBEAT_MS = 15000
const SETTLE_GRACE_MS = 60000

const HEARTBEAT_COMMENT = ':ping\n'

function formatEvent(id: number, data: string): string {
  return `id: ${id}\ndata: ${data}\n\n`
}

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

  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false
      let unsubscribe: () => void = () => {}
      let heartbeat: ReturnType<typeof setInterval> | null = null

      const close = () => {
        if (closed) return
        closed = true
        if (heartbeat) clearInterval(heartbeat)
        heartbeat = null
        unsubscribe()
        unsubscribe = () => {}
        try {
          controller.close()
        } catch {}
      }

      const write = (text: string) => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(text))
        } catch {
          close()
        }
      }

      const writeEnvelope = (event: unknown, eventId: number) => {
        if (closed) return
        write(formatEvent(eventId, JSON.stringify(event)))
        if (closesStream(event as Parameters<typeof closesStream>[0])) {
          unref(setTimeout(() => unregisterBattle(id), SETTLE_GRACE_MS))
          close()
        }
      }

      const lastIdHeader = request.headers.get('last-event-id')
      const parsedLastId = lastIdHeader === null ? -1 : Number(lastIdHeader)
      const lastId = Number.isFinite(parsedLastId) ? Math.trunc(parsedLastId) : -1

      let replayDone = false
      let closedDuringReplay = false
      const pendingEvents: Array<[BattleEvent, number]> = []

      const listener: BattleEventListener = (event, eventId) => {
        if (!replayDone) {
          pendingEvents.push([event, eventId])
          return
        }
        if (eventId <= lastId) return
        writeEnvelope(event, eventId)
      }
      unsubscribe = handle.subscribe(listener)

      const backlog = handle.eventsSince(lastId)
      let nextId = handle.nextId() - backlog.length
      for (const event of backlog) {
        if (closed) {
          closedDuringReplay = true
          break
        }
        writeEnvelope(event, nextId)
        nextId += 1
      }
      replayDone = true

      for (const [event, eventId] of pendingEvents) {
        if (closedDuringReplay) break
        if (eventId <= lastId) continue
        writeEnvelope(event, eventId)
      }

      if (closed) return

      heartbeat = setInterval(() => {
        if (closed) return
        write(HEARTBEAT_COMMENT)
      }, HEARTBEAT_MS)

      request.signal?.addEventListener('abort', close)
    },
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
