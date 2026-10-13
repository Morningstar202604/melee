import { Chess, type Move } from 'chess.js'
import type { Arena, JudgeContext, Submission, Verdict } from './types.ts'

export interface ChessPuzzle {
  id: string
  title: string
  fen: string
  mateIn: 1 | 2
  solution: string
  brief: string
}

export const CHESS_PUZZLES: ChessPuzzle[] = [
  {
    id: 'chess-backrank',
    title: '底线一锤',
    fen: 'Q7/3K1kp1/5p2/8/8/8/8/8 w - - 0 1',
    mateIn: 1,
    solution: 'Qe8#',
    brief: '黑王困守 f7，白后只需找到那条唯一的线。',
  },
  {
    id: 'chess-bare-king',
    title: '荒原孤王',
    fen: '8/8/k7/2K5/8/8/8/1Q6 w - - 0 1',
    mateIn: 1,
    solution: 'Qb6#',
    brief: '白王已经贴身封锁，黑王无处可退。',
  },
  {
    id: 'chess-hunt',
    title: '两步合围',
    fen: '8/3K1kpp/Q7/8/8/8/8/8 w - - 0 1',
    mateIn: 2,
    solution: 'Qe6+',
    brief: '白王贴身逼位，黑方只有 g7/h7 两个兵；两步之内将杀。',
  },
  {
    id: 'chess-long-trip',
    title: '长途奔袭',
    fen: '8/3K1kpp/8/8/8/8/4Q3/8 w - - 0 1',
    mateIn: 2,
    solution: 'Qe6+',
    brief: '皇后远在 e2，需要一步走到正确的位置，然后合围。',
  },
]

const FEN_RE = /((?:[rnbqkpRNBQKP1-8]+\/){7}[rnbqkpRNBQKP1-8]+ w - - 0 1)/

export function chessTopic(puzzle: ChessPuzzle): string {
  return `国际象棋残局「${puzzle.title}」· 白先 ${puzzle.mateIn} 步杀\n${puzzle.brief}\n${puzzle.fen}`
}

export function fenFromTopic(topic: string): string | null {
  const match = topic.match(FEN_RE)
  return match ? match[1]!.trim() : null
}

export function puzzleFromTopic(topic: string): { fen: string; mateIn: 1 | 2; solution: string | null } | null {
  const fen = fenFromTopic(topic)
  if (!fen) return null
  const known = CHESS_PUZZLES.find((puzzle) => puzzle.fen === fen)
  if (known) return { fen, mateIn: known.mateIn, solution: known.solution }
  try {
    return { fen, mateIn: hasMateInOne(fen) ? 1 : 2, solution: null }
  } catch {
    return null
  }
}

function asciiBoard(fen: string): string {
  const board = new Chess(fen).board()
  const lines: string[] = ['  a b c d e f g h']
  for (let index = 0; index < 8; index += 1) {
    const row = board[index] ?? []
    const cells = row.map((cell) => {
      if (!cell) return '.'
      return cell.color === 'w' ? cell.type.toUpperCase() : cell.type
    })
    lines.push(`${8 - index} ${cells.join(' ')}`)
  }
  return lines.join('\n')
}

const UCI_RE = /\b([a-h][1-8])([a-h][1-8])([qrbnQRBN])?\b/
const SAN_SHAPE_RE =
  /^(?:O-O(?:-O)?[+#]?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8][+#]?|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?[+#]?)$/

/** 从自由文本里按出现顺序收集可能的着法（UCI 优先，然后 SAN 形状 token），去重。 */
export function collectMoveCandidates(text: string): string[] {
  if (!text) return []
  const candidates: string[] = []
  const seen = new Set<string>()
  const push = (value: string) => {
    const key = value.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    candidates.push(value)
  }

  const cleaned = text.replace(/[`*_>]/g, ' ')
  const uci = cleaned.match(UCI_RE)
  if (uci) {
    push(`${uci[1]}${uci[2]}${uci[3] ?? ''}`)
  }

  for (const token of cleaned.split(/[^A-Za-z0-9+#=]+/)) {
    if (!token || token.length < 2 || token.length > 7) continue
    if (SAN_SHAPE_RE.test(token)) push(token)
  }
  return candidates
}

export interface MoveAttempt {
  san: string
  from: string
  to: string
  promotion?: string
}

export function applyCandidate(fen: string, candidate: string): MoveAttempt | null {
  const chess = new Chess(fen)
  try {
    const move: Move | null = /^[a-h][1-8][a-h][1-8][qrbnQRBN]?$/.test(candidate)
      ? chess.move({
          from: candidate.slice(0, 2),
          to: candidate.slice(2, 4),
          promotion: candidate[4]?.toLowerCase(),
        })
      : chess.move(candidate)
    if (!move) return null
    return { san: move.san, from: move.from, to: move.to, promotion: move.promotion }
  } catch {
    return null
  }
}

export function hasMateInOne(fen: string): boolean {
  const chess = new Chess(fen)
  for (const move of chess.moves()) {
    const next = new Chess(fen)
    next.move(move)
    if (next.isCheckmate()) return true
  }
  return false
}

/** 白方走 firstSan 之后，黑方无论怎么应，白方下一步都能将杀。 */
export function isForcedMateInTwo(fen: string, firstSan: string): boolean {
  const after = new Chess(fen)
  try {
    after.move(firstSan)
  } catch {
    return false
  }
  if (after.isCheckmate()) return false
  const replies = after.moves()
  if (replies.length === 0) return false
  for (const reply of replies) {
    const branch = new Chess(after.fen())
    branch.move(reply)
    const hasMate = branch.moves().some((mate) => {
      const mateBoard = new Chess(branch.fen())
      mateBoard.move(mate)
      return mateBoard.isCheckmate()
    })
    if (!hasMate) return false
  }
  return true
}

export type ChessAttemptStatus = 'solved' | 'wrong' | 'illegal' | 'missing'

export interface ChessAttemptVerdict {
  status: ChessAttemptStatus
  playedSan: string | null
  detail: string
}

export function verifyChessAttempt(
  fen: string,
  mateIn: 1 | 2,
  text: string,
): ChessAttemptVerdict {
  const candidates = collectMoveCandidates(text)
  if (candidates.length === 0) {
    return { status: 'missing', playedSan: null, detail: '没有给出任何可识别的着法' }
  }

  let firstLegal: MoveAttempt | null = null
  for (const candidate of candidates) {
    const attempt = applyCandidate(fen, candidate)
    if (!attempt) continue
    const solves =
      mateIn === 1 ? isMateInOneMove(fen, attempt.san) : isForcedMateInTwo(fen, attempt.san)
    if (solves) {
      return {
        status: 'solved',
        playedSan: attempt.san,
        detail: `找到 ${mateIn} 步杀法（${attempt.san}）`,
      }
    }
    if (!firstLegal) firstLegal = attempt
  }

  if (firstLegal) {
    return {
      status: 'wrong',
      playedSan: firstLegal.san,
      detail: `着法合法但未能构成 ${mateIn} 步杀（${firstLegal.san}）`,
    }
  }
  return {
    status: 'illegal',
    playedSan: null,
    detail: `给出的着法在棋盘上不合法（${candidates.slice(0, 3).join('、')}）`,
  }
}

function isMateInOneMove(fen: string, san: string): boolean {
  const chess = new Chess(fen)
  try {
    chess.move(san)
  } catch {
    return false
  }
  return chess.isCheckmate()
}

export function createChessArena(): Arena {
  return {
    id: 'chess',
    judgeNeedsLlm: false,
    minContestants: 2,
    maxContestants: 8,
    maxOutputTokens: 64,
    turnTimeoutMs: 60000,
    battleTimeoutMs: 180000,
    buildPrompt: (topic: string) => {
      const puzzle = puzzleFromTopic(topic)
      const mateIn = puzzle?.mateIn ?? 2
      const fen = puzzle?.fen ?? ''
      return {
        system:
          '你是「AI 大乱斗」残局场的选手。你会收到一个国际象棋残局的 FEN 与棋盘图，白方走子。' +
          `请在 ${mateIn} 步内为白方找到将杀（mate）方案。` +
          '只输出一步棋，使用 SAN 记法（例如 Qh7#、Ra8+、Qe6+），不要输出编号、解释或任何多余文字。',
        user: `${topic}\n\n棋盘（大写=白方，小写=黑方，'.'=空格）：\n${fen ? asciiBoard(fen) : ''}`,
      }
    },
    extractCode: () => null,
    judge: async (subs: Submission[], ctx: JudgeContext): Promise<Verdict[]> => {
      const puzzle = puzzleFromTopic(ctx.topic)
      if (!puzzle) {
        return subs.map((sub) => ({
          contestantSlot: sub.contestant.slot,
          scores: { solved: 0 },
          passed: 0,
          total: 1,
          rationale: '题目无法解析（缺 FEN）',
        }))
      }
      return subs.map((sub) => {
        const attempt = verifyChessAttempt(puzzle.fen, puzzle.mateIn, sub.text)
        const solved = attempt.status === 'solved' ? 1 : 0
        return {
          contestantSlot: sub.contestant.slot,
          scores: { solved },
          passed: solved,
          total: 1,
          rationale:
            attempt.status === 'solved' && attempt.playedSan
              ? `${attempt.detail}，本题参考解 ${puzzle.solution ?? attempt.playedSan}`
              : attempt.detail,
        }
      })
    },
  }
}

export const chessArena: Arena = createChessArena()
