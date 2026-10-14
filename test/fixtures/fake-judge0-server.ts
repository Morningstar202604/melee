import { createServer } from 'node:http'

export interface FakeJudge0Item {
  statusId: number
  stdout?: string
  stderr?: string
  compileOutput?: string
  time?: string
}

export interface FakeJudge0Server {
  baseUrl: string
  paths: string[]
  submittedBatches: Array<Array<{ source_code: string; stdin: string; expected_output: string }>>
  queueRounds: number
  close: () => Promise<void>
}

export interface FakeJudge0Options {
  /** 按提交序号返回结果；缺省为全部 Accepted */
  resultFor?: (index: number, submission: { stdin: string; expected_output: string }) => FakeJudge0Item
  /** 前几轮轮询返回"排队中"（status.id=2） */
  queueRounds?: number
}

const DESCRIPTION: Record<number, string> = {
  1: 'In Queue',
  2: 'Processing',
  3: 'Accepted',
  4: 'Wrong Answer',
  5: 'Time Limit Exceeded',
  6: 'Compilation Error',
  7: 'Runtime Error (SIGSEGV)',
  13: 'Internal Error',
}

export function startFakeJudge0(options: FakeJudge0Options = {}): Promise<FakeJudge0Server> {
  const paths: string[] = []
  const submittedBatches: FakeJudge0Server['submittedBatches'] = []
  const queueRounds = options.queueRounds ?? 1
  let pollCount = 0

  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1')
    paths.push(`${req.method} ${url.pathname}`)

    if (req.method === 'POST' && url.pathname === '/submissions/batch') {
      const chunks: Buffer[] = []
      req.on('data', (chunk: Buffer) => chunks.push(chunk))
      req.on('end', () => {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8')) as {
          submissions: Array<{ source_code: string; stdin: string; expected_output: string }>
        }
        submittedBatches.push(body.submissions)
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(
          JSON.stringify({
            submissions: body.submissions.map((_, index) => ({ token: `t${submittedBatches.length - 1}-${index}` })),
          }),
        )
      })
      return
    }

    if (req.method === 'GET' && url.pathname === '/submissions/batch') {
      pollCount += 1
      const tokens = (url.searchParams.get('tokens') ?? '').split(',').filter(Boolean)
      const queued = pollCount <= queueRounds
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(
        JSON.stringify({
          submissions: tokens.map((token, index) => {
            if (queued) {
              return { token, status: { id: 2, description: DESCRIPTION[2] } }
            }
            const batchIndex = Number(token.slice(1).split('-')[0] ?? 0)
            const submission = submittedBatches[batchIndex]?.[index] ?? { stdin: '', expected_output: '' }
            const item =
              options.resultFor?.(index, submission) ?? { statusId: 3, stdout: submission.expected_output, time: '0.05' }
            return {
              token,
              status: { id: item.statusId, description: DESCRIPTION[item.statusId] ?? 'Unknown' },
              stdout: item.stdout ?? null,
              stderr: item.stderr ?? null,
              compile_output: item.compileOutput ?? null,
              time: item.time ?? '0.01',
              memory: null,
            }
          }),
        }),
      )
      return
    }

    res.writeHead(404, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: `fake judge0 未实现 ${req.method} ${url.pathname}` }))
  })

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address !== null ? address.port : 0
      resolve({
        baseUrl: `http://127.0.0.1:${port}`,
        paths,
        submittedBatches,
        queueRounds,
        close: () =>
          new Promise<void>((done) => {
            server.closeAllConnections()
            server.close(() => done())
          }),
      })
    })
  })
}
