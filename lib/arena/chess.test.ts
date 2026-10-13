import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CHESS_PUZZLES,
  applyCandidate,
  chessTopic,
  collectMoveCandidates,
  fenFromTopic,
  hasMateInOne,
  isForcedMateInTwo,
  puzzleFromTopic,
  verifyChessAttempt,
} from './chess.ts'
import type { Submission } from './types.ts'
import { createChessArena } from './chess.ts'

const BACKRANK = CHESS_PUZZLES.find((puzzle) => puzzle.id === 'chess-backrank')!
const HUNT = CHESS_PUZZLES.find((puzzle) => puzzle.id === 'chess-hunt')!

function submission(slot: number, text: string): Submission {
  return {
    contestant: { slot, providerId: 'ollama', modelId: 'm', apiKey: 'k' },
    text,
    code: null,
    usage: null,
    latencyMs: 10,
    status: 'ok',
  }
}

test('题库自检：每题参考解都通过校验，且 mateIn 声明与实际一致', () => {
  for (const puzzle of CHESS_PUZZLES) {
    const attempt = verifyChessAttempt(puzzle.fen, puzzle.mateIn, puzzle.solution)
    assert.equal(attempt.status, 'solved', `${puzzle.id} 参考解未通过：${attempt.detail}`)
    if (puzzle.mateIn === 1) {
      assert.equal(hasMateInOne(puzzle.fen), true, `${puzzle.id} 应存在一步杀`)
    } else {
      assert.equal(hasMateInOne(puzzle.fen), false, `${puzzle.id} 不应存在一步杀（否则题目声明为 2 步）`)
      assert.equal(isForcedMateInTwo(puzzle.fen, puzzle.solution), true, `${puzzle.id} 参考解不是强制两步杀`)
    }
  }
})

test('collectMoveCandidates：UCI 优先、SAN 带将杀符号、去重', () => {
  assert.deepEqual(collectMoveCandidates('Qe8#'), ['Qe8#'])
  assert.deepEqual(collectMoveCandidates('e7e8q'), ['e7e8q'])
  assert.deepEqual(collectMoveCandidates('我认为 Ra8+ 是最好的走法。'), ['Ra8+'])
  assert.deepEqual(collectMoveCandidates('就是一段没有着法的解释'), [])
})

test('extract/apply：合法着法可应用，非法着法返回 null', () => {
  assert.equal(applyCandidate(BACKRANK.fen, 'Qe8#')?.san, 'Qe8#')
  assert.equal(applyCandidate(BACKRANK.fen, 'Qh5'), null)
  assert.equal(applyCandidate(BACKRANK.fen, 'Qz9x'), null)
})

test('verifyChessAttempt：正确解 / 合法但错 / 非法 / 未给出', () => {
  const solved = verifyChessAttempt(BACKRANK.fen, 1, '答案：Qe8#')
  assert.equal(solved.status, 'solved')
  assert.equal(solved.playedSan, 'Qe8#')

  const wrong = verifyChessAttempt(BACKRANK.fen, 1, '我觉得是 Kd6')
  assert.equal(wrong.status, 'wrong')
  assert.equal(wrong.playedSan, 'Kd6')

  const illegal = verifyChessAttempt(BACKRANK.fen, 1, '走 Qh4 吧')
  assert.equal(illegal.status, 'illegal')

  const missing = verifyChessAttempt(BACKRANK.fen, 1, '这题好难，我再想想。')
  assert.equal(missing.status, 'missing')
})

test('verifyChessAttempt：夹带废话时仍能提取出正确着法', () => {
  const attempt = verifyChessAttempt(HUNT.fen, 2, '分析一番后，我的答案是 `Qe6+`，因为可以合围。')
  assert.equal(attempt.status, 'solved')
  assert.equal(attempt.playedSan, 'Qe6+')
})

test('两步杀的"差一步"着法（合法但不强制）判 wrong', () => {
  const attempt = verifyChessAttempt(HUNT.fen, 2, 'Qa1')
  assert.equal(attempt.status, 'wrong')
})

test('topic 编解码：chessTopic 里能解析回 FEN 与 mateIn', () => {
  for (const puzzle of CHESS_PUZZLES) {
    const topic = chessTopic(puzzle)
    assert.equal(fenFromTopic(topic), puzzle.fen)
    const parsed = puzzleFromTopic(topic)
    assert.equal(parsed?.mateIn, puzzle.mateIn)
    assert.equal(parsed?.solution, puzzle.solution)
  }
})

test('chessArena.judge：按着法给出确定性判定并写 rationale', async () => {
  const arena = createChessArena()
  const verdicts = await arena.judge(
    [
      submission(1, 'Qe8#'),
      submission(2, 'Kd6'),
      submission(3, '受不了了我不玩了'),
    ],
    { topic: chessTopic(BACKRANK), judgeModelId: '', judgeApiKey: '' },
  )
  assert.equal(verdicts.length, 3)
  assert.equal(verdicts[0]?.passed, 1)
  assert.equal(verdicts[0]?.scores.solved, 1)
  assert.ok(verdicts[0]?.rationale.includes('Qe8#'))
  assert.equal(verdicts[1]?.passed, 0)
  assert.ok(verdicts[1]?.rationale.includes('未能构成'))
  assert.equal(verdicts[2]?.passed, 0)
  assert.ok(verdicts[2]?.rationale.includes('没有给出'))
})
