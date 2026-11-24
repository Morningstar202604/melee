// 3D 观赛台：只消费与 2D 同一份事件流，不另造一套真相。
// 渲染栈复用 Three.js + OrbitControls（MIT），不引入游戏引擎。
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { api, configure, el, gameMeta, token } from './api.js'
import { connectEvents } from './sse.js'
import { createView, reduce, replayView } from './view.js'
import { applyI18n, resolveLang, setLang, t } from './i18n.js'
import { mountLangSwitch } from './nav.js'
import { installFatalHandlers } from './fatal.js'

installFatalHandlers()
setLang(resolveLang())
applyI18n()
configure()

const params = new URLSearchParams(location.search)
const matchId = params.get('match') || localStorage.getItem('melee.currentMatch') || ''
const stageHost = el('arenaStage')
const WHITE_GLYPHS = new Set(['♔', '♕', '♖', '♗', '♘', '♙'])
const BLACK_GLYPHS = new Set(['♚', '♛', '♜', '♝', '♞', '♟'])
const VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 }
let renderer = null
let scene = null
let camera = null
let controls = null
let boardGroup = null
let stageGroup = null
let pieceLayer = null
let pieceMap = new Map()
let boardSize = 8
let stageSize = 0
let lastBoardKey = ''
let lastPlayersKey = ''
let lastTraceKey = ''
let podGroup = null
let abstractGroup = null
let abstractLabel = null
let view = createView()
let events = []
let closeStream = null
let loading = true
let sceneInfo = null
const tweens = []

/** 颜色只从 arena3d.css 的令牌读，主题/视觉调整不需要改 JS。 */
function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const TEAM_COLORS = ['--a3-team-1', '--a3-team-2', '--a3-team-3', '--a3-team-4', '--a3-team-5', '--a3-team-6', '--a3-team-7', '--a3-team-8'].map(css)

document.title = t('arena3d.title')

function fail(title, detail) {
  el('a3Loading').hidden = true
  el('a3Error').hidden = false
  el('a3ErrorTitle').textContent = title
  el('a3ErrorText').textContent = detail
  el('a3ErrorBack').href = watchHref()
}

function watchHref() {
  if (!matchId) return 'watch.html'
  const query = token() ? `&token=${encodeURIComponent(token())}` : ''
  return `watch.html?match=${encodeURIComponent(matchId)}${query}`
}

el('a3Back').href = watchHref()
el('a3ErrorBack').href = watchHref()
if (el('a3Lang')) mountLangSwitch(el('a3Lang'))

function supportsWebgl() {
  try {
    const probe = document.createElement('canvas')
    return Boolean(probe.getContext('webgl2') || probe.getContext('webgl'))
  } catch {
    return false
  }
}

if (!matchId) {
  fail(t('arena3d.noMatch'), '')
} else if (!supportsWebgl()) {
  fail(t('arena3d.noWebgl'), t('arena3d.noWebglHint'))
} else {
  try {
    initThree()
    loadMatch()
  } catch (error) {
    fail(t('arena3d.noWebgl'), String(error.message || error))
  }
}

function initThree() {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(window.innerWidth, window.innerHeight)
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.08
  stageHost.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  scene.background = new THREE.Color(css('--a3-space'))
  scene.fog = new THREE.FogExp2(css('--a3-space'), 0.028)

  camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 160)
  scene.add(new THREE.AmbientLight(css('--a3-ambient'), 0.24))
  const hemi = new THREE.HemisphereLight(css('--a3-hemi'), css('--a3-ground'), 0.55)
  scene.add(hemi)
  const key = new THREE.DirectionalLight(css('--a3-key'), 1.25)
  key.position.set(7, 12, 6)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  key.shadow.camera.left = -12
  key.shadow.camera.right = 12
  key.shadow.camera.top = 12
  key.shadow.camera.bottom = -12
  scene.add(key)
  const fill = new THREE.PointLight(css('--a3-cyan'), 18, 24, 2)
  fill.position.set(-7, 5, -4)
  scene.add(fill)
  const warm = new THREE.PointLight(css('--a3-gold'), 14, 22, 2)
  warm.position.set(6, 3, -5)
  scene.add(warm)

  boardGroup = new THREE.Group()
  pieceLayer = new THREE.Group()
  boardGroup.add(pieceLayer)
  scene.add(boardGroup)
  podGroup = new THREE.Group()
  scene.add(podGroup)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.075
  controls.enablePan = true
  controls.minDistance = 5
  controls.maxDistance = 32
  controls.maxPolarAngle = Math.PI * 0.49
  controls.autoRotate = true
  controls.autoRotateSpeed = 0.35
  controls.target.set(0, 0.1, 0)

  attachCameraButtons()
  window.addEventListener('resize', onResize)
  renderer.setAnimationLoop(loop)
}

function onResize() {
  if (!renderer || !camera) return
  camera.aspect = window.innerWidth / window.innerHeight
  camera.updateProjectionMatrix()
  renderer.setSize(window.innerWidth, window.innerHeight)
}

async function loadMatch() {
  try {
    // 事件快照只取尾部 320 条用于动画；选手名单、赛制与终态以 /scene 为准，
    // 否则长对局（事件多过 320）会丢 match.created，3D 场景既没有选手也没有教练目标。
    sceneInfo = await api.scene(matchId)
    const snapshot = await api.events(matchId, -1, 320)
    events = snapshot.events || []
    view = replayView(events)
    view.players = (sceneInfo.players || []).length ? sceneInfo.players : view.players
    view.gameKind = view.gameKind || sceneInfo.game
    view.imported = Boolean(view.imported || sceneInfo.source === 'import' || sceneInfo.source?.imported)
    if (sceneInfo.finished) view.status = 'ended'
    else if (sceneInfo.status === 'live' && view.status === 'waiting') view.status = 'live'
    lastBoardKey = ''
    loading = false
    sync()
    attachCoachForm()
    el('a3Loading').hidden = true
    if (view.status !== 'ended') {
      closeStream = await connectEvents(matchId, {
        after: events.length ? events[events.length - 1].seq : -1,
        onEvent: (event) => {
          if (events.length && event.seq <= events[events.length - 1].seq) return
          events = events.concat(event)
          view = reduce(view, event)
          sync()
          if (event.type === 'match.ended') {
            closeStream?.()
            closeStream = null
          }
        },
        onError: () => {
          el('a3Conn').textContent = t('overlay.disconnected')
        },
        onOpen: () => {
          el('a3Conn').textContent = t('overlay.live')
        },
      })
    }
  } catch (error) {
    fail('3D arena', String(error.message || error))
  }
}

function sync() {
  if (loading) return
  const board = currentBoard()
  const key = boardKey(board)
  if (key !== lastBoardKey) {
    setBoard(board)
    lastBoardKey = key
  }
  updatePlayers(view.players || [])
  setHud()
  renderCoachForm()
  renderTrace()
}

function currentBoard() {
  const state = view.gameState || view.finalState?.gameState || {}
  return Array.isArray(state.board) ? state.board : []
}

function boardKey(board) {
  if (!board.length) return 'empty'
  return board.length + 'x' + (board[0]?.length || 0) + ':' + board.map((row) => row.join('|')).join('/')
}

function setHud() {
  const status = view.status || 'waiting'
  const statusEl = el('a3Status')
  statusEl.textContent = t(`watch.status.${status}`)
  statusEl.className = 'a3-pill ' + status
  const meta = gameMeta(view.gameKind)
  el('a3Game').textContent = meta.label
  el('a3Turn').textContent = `${t('arena3d.turn')} ${view.turnIndex ?? 0}`
  el('a3Events').textContent = String(events.length)
  const last = view.gameState?.lastMove
  el('a3Last').textContent = last?.from ? `${last.from} → ${last.to}` : (view.lastAction?.value || '—')
  const material = materialOf(currentBoard())
  el('a3Material').textContent = material ? `${material.diff >= 0 ? '+' : ''}${material.diff}` : '—'

  const narration = view.narration.length ? view.narration[view.narration.length - 1].text : t('watch.waitingFirst')
  el('a3Narration').textContent = narration
  el('a3Conn').textContent = view.status === 'ended' ? t('watch.conn.ended') : t('overlay.live')
  el('a3Conn').style.color = view.status === 'ended' ? 'var(--a3-green)' : 'var(--a3-red)'

  const coachEl = el('a3Coach')
  const rows = (view.coach || []).slice(-3)
  if (!rows.length) {
    coachEl.textContent = t('watch.coach.empty')
  } else {
    coachEl.replaceChildren(...rows.map((row) => {
      const line = document.createElement('div')
      const who = document.createElement('b')
      who.textContent = row.target
      const status = t(row.status === 'delivered' ? 'arena3d.coachDelivered' : 'arena3d.coachQueued')
      line.append(who, document.createTextNode(` · ${status} · ${row.instruction}`))
      return line
    }))
  }
}

function renderCoachForm() {
  const form = el('a3CoachForm')
  if (!form) return
  const live = view.status === 'live' && !view.imported
  form.hidden = !live
  if (!live) return
  const target = el('a3CoachTarget')
  const key = (view.players || []).join('|')
  if (target.dataset.key !== key) {
    target.dataset.key = key
    target.replaceChildren(...(view.players || []).map((player) => {
      const option = document.createElement('option')
      option.value = player
      option.textContent = player
      return option
    }))
  }
}

function renderTrace() {
  const thoughtsEl = el('a3Thoughts')
  const timelineEl = el('a3Timeline')
  if (!thoughtsEl || !timelineEl) return
  const players = view.players || []
  const signature = [
    events.length,
    view.seq,
    view.activePlayer || '',
    ...players.map((player) => `${player}:${(view.lastThoughts?.[player] || '').length}`),
  ].join('|')
  if (signature === lastTraceKey) return
  lastTraceKey = signature

  const visible = players
    .map((player, index) => ({ player, index, text: (view.lastThoughts?.[player] || '').trim() }))
    .filter((row) => row.text || (view.status === 'live' && view.activePlayer === row.player))
  if (!visible.length) {
    const empty = document.createElement('p')
    empty.className = 'a3-traceempty'
    empty.textContent = t('arena3d.noThought')
    thoughtsEl.replaceChildren(empty)
  } else {
    thoughtsEl.replaceChildren(...visible.map((row) => {
      const item = document.createElement('div')
      item.className = 'a3-thought' + (view.activePlayer === row.player && view.status === 'live' ? ' active' : '')
      const head = document.createElement('div')
      head.className = 'a3-thoughthead'
      const dot = document.createElement('i')
      dot.style.background = TEAM_COLORS[row.index % TEAM_COLORS.length]
      const name = document.createElement('b')
      name.textContent = row.player
      head.append(dot, name)
      if (view.activePlayer === row.player && view.status === 'live') {
        const now = document.createElement('span')
        now.textContent = t('arena3d.thinking')
        head.append(now)
      }
      const text = document.createElement('p')
      text.textContent = row.text || t('arena3d.thinkingNow')
      item.append(head, text)
      return item
    }))
  }

  const trace = events
    .filter((event) => (
      event.type === 'turn.started' ||
      event.type === 'action.proposed' ||
      event.type === 'action.applied' ||
      event.type === 'narration.line' ||
      event.type === 'match.ended' ||
      (event.type === 'coach.message' && event.payload?.status !== 'queued')
    ))
    .slice(-7)
  if (!trace.length) {
    const empty = document.createElement('li')
    empty.className = 'a3-traceempty'
    empty.textContent = t('arena3d.traceEmpty')
    timelineEl.replaceChildren(empty)
    return
  }
  timelineEl.replaceChildren(...trace.map((event) => {
    const item = document.createElement('li')
    const seq = document.createElement('span')
    seq.className = 'a3-tseq'
    seq.textContent = `#${event.seq}`
    const text = document.createElement('span')
    text.className = 'a3-ttext ' + (event.type === 'coach.message' ? 'coach' : event.type.split('.')[0])
    text.textContent = traceText(event)
    text.title = text.textContent
    item.append(seq, text)
    return item
  }))
}

function traceText(event) {
  const payload = event.payload || {}
  const value = payload.action?.value ?? payload.action?.kind ?? ''
  switch (event.type) {
    case 'turn.started': {
      const turn = payload.turnIndex ?? payload.turn
      return `${event.player || '—'} · ${turn == null ? t('arena3d.turnStart') : `${t('arena3d.turn')} ${turn}`}`
    }
    case 'action.proposed':
      return `${event.player || '—'} · ${String(value)}`
    case 'action.applied':
      return `${event.player || '—'} · ${String(value)} ✓`
    case 'coach.message':
      return `⚑ ${payload.target || '—'} · ${payload.instruction || ''}`
    case 'narration.line':
      return String(payload.text || '')
    case 'match.ended':
      return `${t('watch.status.ended')} · ${String(payload.winner ?? payload.reason ?? '')}`
    default:
      return event.type
  }
}

function attachCoachForm() {
  const form = el('a3CoachForm')
  if (!form || form.dataset.bound) return
  form.dataset.bound = '1'
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    const target = el('a3CoachTarget').value
    const instruction = el('a3CoachText').value.trim()
    const error = el('a3CoachError')
    error.hidden = true
    if (!target || !instruction) return
    const button = el('a3CoachSend')
    button.disabled = true
    try {
      const payload = await api.coach(matchId, target, instruction)
      el('a3CoachText').value = ''
      const commandEvent = payload.event
      if (commandEvent && !(events.length && commandEvent.seq <= events[events.length - 1].seq)) {
        events = events.concat(commandEvent)
        view = reduce(view, commandEvent)
        sync()
      }
    } catch (err) {
      error.textContent = `${t('arena3d.coachFailed')}: ${String(err.message || err)}`
      error.hidden = false
    } finally {
      button.disabled = false
    }
  })
}

function updatePlayers(players) {
  const key = players.join('|')
  if (key === lastPlayersKey) return
  lastPlayersKey = key
  clearGroup(podGroup)
  const colors = TEAM_COLORS
  // 小棋盘 pod 同时收半径和体积：3×3 不再让两个大圆盘挤满画面。
  const radius = Math.min(11, Math.max(3.1, boardSize * (boardSize <= 4 ? 1.25 : 0.78)))
  const scale = Math.min(1, Math.max(0.32, boardSize / 12))
  players.forEach((player, index) => {
    const angle = players.length === 1 ? 0 : (Math.PI * 2 * index) / players.length - Math.PI / 2
    const x = Math.cos(angle) * radius
    const z = Math.sin(angle) * radius
    const color = new THREE.Color(colors[index % colors.length])
    const pod = new THREE.Mesh(
      new THREE.CylinderGeometry(0.62 * scale, 0.72 * scale, 0.22 * scale, 32),
      new THREE.MeshStandardMaterial({ color: css('--a3-pod'), metalness: 0.55, roughness: 0.3, emissive: color, emissiveIntensity: 0.14 }),
    )
    pod.position.set(x, 0.1 * scale, z)
    pod.castShadow = true
    podGroup.add(pod)
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.58 * scale, 0.035 * scale, 8, 48),
      new THREE.MeshBasicMaterial({ color }),
    )
    ring.rotation.x = Math.PI / 2
    ring.position.set(x, 0.22 * scale, z)
    podGroup.add(ring)
    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(0.015 * scale, 0.015 * scale, 1.55 * scale, 8),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28 }),
    )
    beam.position.set(x, 0.95 * scale, z)
    podGroup.add(beam)
    const label = textSprite(player, { color: css('--a3-white'), height: 0.42 * scale, maxWidth: 2.4 * scale, font: 700 })
    label.position.set(x, 1.32 * scale, z)
    podGroup.add(label)
  })
}

function materialOf(board) {
  if (!Array.isArray(board) || !board.length) return null
  let white = 0
  let black = 0
  for (const row of board) {
    if (!Array.isArray(row)) continue
    for (const cell of row) {
      const glyph = String(cell || '')
      const lower = glyph.toLowerCase()
      const value = VALUES[lower] || 0
      if (WHITE_GLYPHS.has(glyph) || (glyph && /[A-Z]/.test(glyph)) ) white += value
      else if (BLACK_GLYPHS.has(glyph) || (glyph && /[a-z]/.test(glyph)) ) black += value
    }
  }
  return { white, black, diff: white - black }
}

function isWhitePiece(glyph) {
  if (WHITE_GLYPHS.has(glyph)) return true
  return /[A-Z]/.test(glyph) && !BLACK_GLYPHS.has(glyph) && glyph.toLowerCase() in VALUES
}

function setBoard(board) {
  if (!board.length) {
    if (boardGroup) boardGroup.visible = false
    showAbstractStage()
    lastBoardKey = 'empty'
    return
  }
  if (boardGroup) boardGroup.visible = true
  if (abstractGroup) abstractGroup.visible = false
  const rows = board.length
  const cols = board[0]?.length || rows
  if (!board.length || rows !== stageSize) {
    rebuildBoard(rows, cols)
  }
  const cell = cellSize(rows, cols)
  const originX = -((cols - 1) * cell) / 2
  const originZ = -((rows - 1) * cell) / 2
  const old = pieceMap
  const used = new Set()
  const nextMap = new Map()

  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const glyph = String(board[r]?.[c] || '')
      if (!glyph || glyph === '·' || glyph === '.') continue
      const key = `${r}:${c}`
      const exact = old.get(key)
      if (exact && exact.userData.glyph === glyph) {
        used.add(key)
        nextMap.set(key, exact)
        continue
      }
      const matched = findNearest(old, used, glyph, r, c)
      if (matched) {
        used.add(matched.key)
        matched.group.userData.glyph = glyph
        nextMap.set(key, matched.group)
        tweenPosition(matched.group, new THREE.Vector3(originX + c * cell, 0, originZ + r * cell), 620)
        continue
      }
      const group = makePiece(glyph, cell)
      group.position.set(originX + c * cell, 0, originZ + r * cell)
      group.scale.setScalar(0.01)
      pieceLayer.add(group)
      tweenScale(group, 1, 440)
      nextMap.set(key, group)
    }
  }

  for (const [key, group] of old.entries()) {
    if (used.has(key) || nextMap.get(key) === group) continue
    if (group.userData.removing) continue
    group.userData.removing = true
    tweenScale(group, 0.01, 300, () => {
      if (group.parent) group.parent.remove(group)
      disposeObject(group)
    })
  }
  pieceMap = nextMap
}

function findNearest(oldMap, used, glyph, row, col) {
  let best = null
  let distance = Infinity
  for (const [key, group] of oldMap.entries()) {
    if (used.has(key) || group.userData.glyph !== glyph || group.userData.removing) continue
    const [r, c] = key.split(':').map(Number)
    const d = Math.abs(r - row) + Math.abs(c - col)
    if (d < distance) {
      best = { key, group }
      distance = d
    }
  }
  return best
}

function cellSize(rows, cols) {
  const span = Math.max(rows, cols)
  return Math.min(1.2, 8.4 / span)
}

function rebuildBoard(rows, cols) {
  if (!boardGroup) return
  boardGroup.visible = true
  clearGroup(pieceLayer)
  for (const child of [...boardGroup.children]) {
    if (child !== pieceLayer) {
      boardGroup.remove(child)
      disposeObject(child)
    }
  }
  pieceMap = new Map()
  stageSize = rows
  boardSize = Math.max(rows, cols)
  const cell = cellSize(rows, cols)
  const originX = -((cols - 1) * cell) / 2
  const originZ = -((rows - 1) * cell) / 2
  const boardRadius = Math.max(boardSize * cell * 0.78, 3.6)

  const tileGeo = new THREE.BoxGeometry(cell * 0.96, 0.12, cell * 0.96)
  const lightMat = new THREE.MeshStandardMaterial({ color: css('--a3-board-light'), roughness: 0.7, metalness: 0.05 })
  const darkMat = new THREE.MeshStandardMaterial({ color: css('--a3-board-dark'), roughness: 0.72, metalness: 0.12 })
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const tile = new THREE.Mesh(tileGeo, (r + c) % 2 ? darkMat : lightMat)
      tile.position.set(originX + c * cell, -0.06, originZ + r * cell)
      tile.receiveShadow = true
      boardGroup.add(tile)
    }
  }

  if (!stageGroup) {
    stageGroup = new THREE.Group()
    scene.add(stageGroup)
  }
  clearGroup(stageGroup)
  const disc = new THREE.Mesh(
    new THREE.CylinderGeometry(boardRadius + 1.4, boardRadius + 1.7, 0.42, 72),
    new THREE.MeshStandardMaterial({ color: css('--a3-stage'), metalness: 0.5, roughness: 0.42 }),
  )
  disc.position.y = -0.33
  disc.receiveShadow = true
  stageGroup.add(disc)
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(boardRadius + 1.15, 0.035, 8, 96),
    new THREE.MeshBasicMaterial({ color: css('--a3-cyan'), transparent: true, opacity: 0.38 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = -0.08
  stageGroup.add(ring)
  const stars = new THREE.BufferGeometry()
  const positions = []
  for (let i = 0; i < 260; i += 1) {
    const angle = Math.random() * Math.PI * 2
    const radius = 7 + Math.random() * 16
    positions.push(Math.cos(angle) * radius, 1.5 + Math.random() * 9, Math.sin(angle) * radius)
  }
  stars.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  const starPoints = new THREE.Points(stars, new THREE.PointsMaterial({ color: css('--a3-star'), size: 0.04, transparent: true, opacity: 0.7 }))
  stageGroup.add(starPoints)
  if (rows === 8 && cols === 8) addCoordinates(cell, originX, originZ)

  // 主光源随棋盘大小移动，避免大棋盘边缘发黑。
  const key = scene.children.find((item) => item.isDirectionalLight)
  if (key) key.position.set(boardRadius + 4, boardRadius * 1.7, boardRadius + 3)
  setCameraPreset('broadcast', true)
}

function addCoordinates(cell, originX, originZ) {
  const y = 0.18
  for (let c = 0; c < 8; c += 1) {
    const label = textSprite(String.fromCharCode(97 + c), { color: css('--a3-coord'), height: 0.22, font: 600 })
    label.position.set(originX + c * cell, y, originZ + 8 * cell - 0.16)
    boardGroup.add(label)
  }
  for (let r = 0; r < 8; r += 1) {
    const label = textSprite(String(8 - r), { color: css('--a3-coord'), height: 0.22, font: 600 })
    label.position.set(originX - 0.22, y, originZ + r * cell)
    boardGroup.add(label)
  }
}

function showAbstractStage() {
  if (!scene) return
  if (!abstractGroup) {
    abstractGroup = new THREE.Group()
    const core = new THREE.Mesh(
      new THREE.OctahedronGeometry(1.15, 1),
      new THREE.MeshStandardMaterial({
        color: css('--a3-core'),
        metalness: 0.65,
        roughness: 0.22,
        emissive: css('--a3-cyan'),
        emissiveIntensity: 0.35,
        wireframe: true,
      }),
    )
    core.position.y = 1.9
    abstractGroup.add(core)
    const shell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(1.55, 1),
      new THREE.MeshBasicMaterial({ color: css('--a3-cyan'), wireframe: true, transparent: true, opacity: 0.16 }),
    )
    shell.position.y = 1.9
    abstractGroup.add(shell)
    for (const [radius, y, color] of [[2.2, 0.35, css('--a3-gold')], [2.7, 1.9, css('--a3-cyan')]]) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(radius, 0.025, 6, 72),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55 }),
      )
      ring.rotation.x = Math.PI / 2
      ring.position.y = y
      abstractGroup.add(ring)
    }
    abstractLabel = textSprite('ARENA', { color: css('--a3-white'), height: 0.42, maxWidth: 2.6, font: 800 })
    abstractLabel.position.set(0, 3.45, 0)
    abstractGroup.add(abstractLabel)
    scene.add(abstractGroup)
  }
  abstractGroup.visible = true
  const label = gameMeta(view.gameKind).label
  if (label && abstractLabel) {
    const next = textSprite(label.toUpperCase(), { color: css('--a3-white'), height: 0.42, maxWidth: 3.4, font: 800 })
    next.position.copy(abstractLabel.position)
    abstractGroup.remove(abstractLabel)
    disposeObject(abstractLabel)
    abstractGroup.add(next)
    abstractLabel = next
  }
  setCameraPreset('broadcast', true)
}

function makePiece(glyph, cell) {
  const group = new THREE.Group()
  group.userData.glyph = glyph
  const white = isWhitePiece(glyph)
  const accent = new THREE.Color(white ? css('--a3-white-soft') : css('--a3-cyan'))
  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(cell * 0.26, cell * 0.32, 0.14, 28),
    new THREE.MeshStandardMaterial({
      color: white ? css('--a3-piece-light') : css('--a3-piece-dark'),
      metalness: 0.52,
      roughness: 0.27,
      emissive: accent,
      emissiveIntensity: white ? 0.12 : 0.28,
    }),
  )
  base.position.y = 0.08
  base.castShadow = true
  group.add(base)
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(cell * 0.28, 0.016, 6, 40),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.8 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.16
  group.add(ring)
  const label = textSprite(glyph, { color: white ? css('--a3-white-soft') : css('--a3-cyan-soft'), height: cell * 0.52, maxWidth: cell * 0.96, font: 800 })
  label.position.y = cell * 0.56
  group.add(label)
  return group
}

function textSprite(text, { color = '#fff', height = 0.5, maxWidth = 2.4, font = 700 } = {}) {
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 128
  const size = 84
  let ctx = canvas.getContext('2d')
  ctx.font = `${font} ${size}px "Noto Sans Symbols 2","Segoe UI Symbol",system-ui,sans-serif`
  const measured = Math.max(1, ctx.measureText(text).width)
  // 固定 256px 画布会把长选手名（GPT-5.5、Gemini…）截掉左右；按实际字宽扩容。
  canvas.width = Math.min(1024, Math.ceil(measured + 32))
  canvas.height = 128
  ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.font = `${font} ${size}px "Noto Sans Symbols 2","Segoe UI Symbol",system-ui,sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const scale = Math.min(1, (canvas.width - 18) / measured)
  ctx.fillStyle = color
  ctx.shadowColor = color
  ctx.shadowBlur = 8
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.minFilter = THREE.LinearFilter
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, depthTest: false })
  const sprite = new THREE.Sprite(material)
  const width = Math.min(maxWidth, Math.max(height, (measured / size) * height * scale))
  sprite.scale.set(width, height, 1)
  sprite.renderOrder = 3
  return sprite
}

function tweenPosition(group, to, ms) {
  const from = group.position.clone()
  if (from.distanceTo(to) < 0.001) return
  tweens.push({ group, kind: 'pos', from, to, start: performance.now(), ms, ease: easeOutCubic })
}

function tweenScale(group, to, ms, done) {
  const from = group.scale.x || 1
  tweens.push({ group, kind: 'scale', from, to, start: performance.now(), ms, done, ease: easeOutCubic })
}

function easeOutCubic(x) {
  return 1 - (1 - x) ** 3
}

function loop() {
  const now = performance.now()
  for (let i = tweens.length - 1; i >= 0; i -= 1) {
    const item = tweens[i]
    const t = Math.min(1, (now - item.start) / item.ms)
    const e = item.ease(t)
    if (item.kind === 'pos') item.group.position.lerpVectors(item.from, item.to, e)
    else item.group.scale.setScalar(item.from + (item.to - item.from) * e)
    if (t >= 1) {
      tweens.splice(i, 1)
      item.done?.()
    }
  }
  controls?.update()
  renderer?.render(scene, camera)
}

function setCameraPreset(name, instant = false) {
  if (!camera || !controls) return
  controls.autoRotate = false
  el('a3Auto')?.setAttribute('aria-pressed', 'false')
  for (const button of document.querySelectorAll('.a3-cam[data-camera]')) {
    button.classList.toggle('on', button.dataset.camera === name)
  }
  const r = Math.max(5, boardSize * 0.8)
  const presets = {
    broadcast: { pos: [r * 1.55, r * 1.35, r * 1.9], target: [0, 0.1, 0] },
    top: { pos: [0.01, r * 2.7, 0.6], target: [0, 0.1, 0] },
    side: { pos: [r * 2.25, r * 0.55, 0.2], target: [0, 0.1, 0] },
    free: { pos: [camera.position.x, camera.position.y, camera.position.z], target: [controls.target.x, controls.target.y, controls.target.z] },
  }
  const preset = presets[name] || presets.broadcast
  if (name === 'free') return
  const to = new THREE.Vector3(...preset.pos)
  const target = new THREE.Vector3(...preset.target)
  if (instant) {
    camera.position.copy(to)
    controls.target.copy(target)
    controls.update()
    return
  }
  tweenCamera(to, target)
}

function tweenCamera(to, target) {
  const from = camera.position.clone()
  const fromTarget = controls.target.clone()
  const start = performance.now()
  const animate = () => {
    const t = Math.min(1, (performance.now() - start) / 620)
    const e = easeOutCubic(t)
    camera.position.lerpVectors(from, to, e)
    controls.target.lerpVectors(fromTarget, target, e)
    controls.update()
    if (t < 1) requestAnimationFrame(animate)
  }
  animate()
}

function attachCameraButtons() {
  for (const button of document.querySelectorAll('.a3-cam[data-camera]')) {
    button.addEventListener('click', () => setCameraPreset(button.dataset.camera))
  }
  el('a3Auto').addEventListener('click', () => {
    controls.autoRotate = !controls.autoRotate
    el('a3Auto').setAttribute('aria-pressed', String(controls.autoRotate))
    if (controls.autoRotate) controls.autoRotateSpeed = 0.35
  })
}

function clearGroup(group) {
  if (!group) return
  for (const child of [...group.children]) {
    group.remove(child)
    disposeObject(child)
  }
}

function disposeObject(object) {
  object.traverse?.((child) => {
    child.geometry?.dispose?.()
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    for (const material of materials) {
      material?.map?.dispose?.()
      material?.dispose?.()
    }
  })
}
