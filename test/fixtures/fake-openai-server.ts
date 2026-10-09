import { createServer } from 'node:http'

export interface FakeOpenAIServer {
  baseUrl: string
  paths: string[]
  setStatus: (status: number | null) => void
  close: () => Promise<void>
}

export interface FakeServerOptions {
  chunks?: string[]
  failWith?: number
  latencyMs?: number
}

const DEFAULT_CHUNKS = ['你', '好']
const PROMPT_TOKENS = 7
const COMPLETION_TOKENS = 2

function sse(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`
}

export function startFakeServer(options: FakeServerOptions = {}): Promise<FakeOpenAIServer> {
  const chunks = options.chunks ?? DEFAULT_CHUNKS
  const latency = options.latencyMs ?? 10
  const paths: string[] = []
  let forcedStatus: number | undefined = options.failWith

  const server = createServer((req, res) => {
    const path = (req.url ?? '/').split('?')[0]
    paths.push(path)
    const bodyChunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => bodyChunks.push(chunk))
    req.on('end', () => {
      if (path.endsWith('/models')) {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(
          JSON.stringify({
            object: 'list',
            data: [{ id: 'fake-model-b', object: 'model' }, { id: 'fake-model-a', object: 'model' }],
          }),
        )
        return
      }

      if (path.endsWith('/chat/completions')) {
        if (forcedStatus !== undefined) {
          const auth = req.headers.authorization ?? ''
          res.writeHead(forcedStatus, { 'content-type': 'application/json' })
          res.end(
            JSON.stringify({
              error: { message: `fake ${forcedStatus}，收到的凭证是 ${auth}`, type: 'fake_error' },
            }),
          )
          return
        }

        res.writeHead(200, {
          'content-type': 'text/event-stream',
          'cache-control': 'no-cache',
          connection: 'keep-alive',
        })

        let index = 0
        const timer = setInterval(() => {
          if (index < chunks.length) {
            res.write(
              sse({
                id: 'fake',
                object: 'chat.completion.chunk',
                model: 'fake-model',
                choices: [{ index: 0, delta: { content: chunks[index] }, finish_reason: null }],
              }),
            )
            index += 1
            return
          }
          clearInterval(timer)
          res.write(sse({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }))
          res.write(
            sse({
              choices: [],
              usage: {
                prompt_tokens: PROMPT_TOKENS,
                completion_tokens: COMPLETION_TOKENS,
                total_tokens: PROMPT_TOKENS + COMPLETION_TOKENS,
              },
            }),
          )
          res.write('data: [DONE]\n\n')
          res.end()
        }, latency)

        res.on('close', () => clearInterval(timer))
        return
      }

      res.writeHead(404, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: { message: `fake server 未实现 ${path}` } }))
    })
  })

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address !== null ? address.port : 0
      resolve({
        baseUrl: `http://127.0.0.1:${port}`,
        paths,
        setStatus: (status: number | null) => {
          forcedStatus = status ?? undefined
        },
        close: () =>
          new Promise<void>((done) => {
            server.closeAllConnections()
            server.close(() => done())
          }),
      })
    })
  })
}
