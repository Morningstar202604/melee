// 事件流 → 视图状态。纯函数，直播与回放共用同一份。
// 任何画面都必须能由它算出来；有这一条，回放才可能和直播一致。

export function createView() {
  return {
    gameKind: 'twenty-questions', // 决定中央面板用哪种皮肤
    status: 'waiting', // waiting | live | ended
    players: [],
    turnIndex: 0,
    maxQuestions: 21,
    remaining: null,
    history: [],
    thoughts: {}, // player -> string[]
    currentThought: null,
    lastAction: null,
    narration: [],
    cues: [],
    winner: null,
    endReason: '',
    rejected: 0,
    seq: -1,
  }
}

export function reduce(view, event) {
  const next = { ...view, seq: event.seq }
  const payload = event.payload || {}

  switch (event.type) {
    case 'match.created':
      next.players = payload.players || []
      next.gameKind = payload.game || 'twenty-questions'
      next.status = 'live'
      break

    case 'match.started':
      next.firstPlayer = payload.firstPlayer
      break

    case 'turn.started':
      next.activePlayer = event.player || null
      next.currentThought = null
      break

    case 'thought.delta': {
      const player = event.player
      const list = (next.thoughts[player] || []).concat(payload.text)
      next.thoughts = { ...next.thoughts, [player]: list }
      next.currentThought = { player, text: payload.text }
      break
    }

    case 'action.proposed':
      next.lastAction = {
        player: event.player,
        kind: payload.action?.kind,
        value: payload.action?.value,
        raw: payload.raw,
        seq: event.seq,
      }
      break

    case 'action.rejected':
      next.rejected += 1
      next.lastRejection = { player: event.player, reason: payload.reason }
      break

    case 'action.applied':
      next.lastAction = {
        ...next.lastAction,
        player: event.player,
        kind: payload.action?.kind,
        value: payload.action?.value,
        settled: true,
        effects: payload.effects,
        seq: event.seq,
      }
      break

    case 'state.changed': {
      const pub = payload.publicState || {}
      next.turnIndex = pub.turnIndex ?? next.turnIndex
      next.maxQuestions = pub.maxQuestions ?? next.maxQuestions
      next.remaining = pub.remainingPublic ?? next.remaining
      next.history = pub.history || next.history
      // 棋类把局面放在 gameState，代号直接放 board —— 两种都收。
      if (pub.gameState) next.gameState = pub.gameState
      if (Array.isArray(pub.board)) next.gameState = { ...(next.gameState || {}), board: pub.board }
      if (pub.turn !== undefined) next.turn = pub.turn
      if (pub.currentPlayer !== undefined) next.currentPlayer = pub.currentPlayer
      if (pub.phase !== undefined) next.phase = pub.phase
      if (pub.hint !== undefined) next.hint = pub.hint
      if (pub.remaining !== undefined) next.teamRemaining = pub.remaining
      if (pub.finished) next.status = 'ended'
      break
    }

    case 'narration.line':
      next.narration = next.narration.concat({
        text: payload.text,
        tone: payload.tone || 'neutral',
        refs: payload.refs || [],
        seq: event.seq,
      })
      break

    case 'director.cue':
      next.cues = next.cues.concat({
        shot: payload.shot,
        target: payload.target ?? null,
        durationMs: payload.durationMs,
        trigger: payload.trigger,
        seq: event.seq,
      })
      break

    case 'match.ended':
      next.status = 'ended'
      next.winner = payload.winner
      next.endReason = payload.reason
      next.finalState = payload.finalState
      if (payload.finalState && payload.finalState.gameState) {
        next.gameState = payload.finalState.gameState
      }
      next.activePlayer = null
      break

    default:
      break
  }

  return next
}

export function replayView(events, upTo = events.length) {
  let view = createView()
  for (let i = 0; i < Math.min(upTo, events.length); i += 1) {
    view = reduce(view, events[i])
  }
  return view
}

export function currentCue(view) {
  return view.cues.length ? view.cues[view.cues.length - 1] : null
}

export function latestThoughtOf(view, player) {
  const list = view.thoughts[player] || []
  return list.length ? list[list.length - 1] : ''
}
