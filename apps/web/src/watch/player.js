// 播放控制：直播/回放/倍速/时间轴。只操作游标与定时器，不碰 DOM 结构。
// 与 render.js 的分工：这里决定"看到第几条"，render 决定"这条长什么样"。

export function createPlayer({ getEvents, render, onChange } = {}) {
  const state = {
    mode: 'live', // live | replay
    playing: true,
    speed: 1,
    cursor: 0,
    dismissedEnd: false,
  }
  let frame = null
  let timer = null

  const events = () => (typeof getEvents === 'function' ? getEvents() : [])
  const notify = () => {
    if (typeof render === 'function') render()
    if (typeof onChange === 'function') onChange(state)
  }

  function stopLoop() {
    cancelAnimationFrame(frame)
    clearTimeout(timer)
    frame = null
    timer = null
  }

  function startLoop() {
    stopLoop()
    const tick = () => {
      if (state.mode === 'replay' && state.playing && state.cursor < events().length) {
        state.cursor = Math.min(events().length, state.cursor + 1)
        notify()
      }
      // setTimeout 句柄必须留着：只 cancelAnimationFrame 挡不住已经排队的定时器，
      // 重复调用会叠出多条并行循环。
      timer = setTimeout(() => {
        frame = requestAnimationFrame(tick)
      }, 900 / state.speed)
    }
    tick()
  }

  return {
    state,
    start: startLoop,
    stop: stopLoop,
    /** 接上一场比赛：回到直播态，游标追平。 */
    attach(total) {
      state.mode = 'live'
      state.playing = true
      state.cursor = total
      state.dismissedEnd = false
      notify()
    },
    pause() {
      if (state.mode === 'live') {
        state.mode = 'replay'
        state.cursor = events().length
      }
      state.playing = !state.playing
      notify()
      return state.playing
    },
    seek(position) {
      state.mode = 'replay'
      state.playing = false
      state.cursor = Number(position) || 0
      notify()
    },
    step(delta) {
      state.mode = 'replay'
      state.playing = false
      state.cursor = Math.max(0, Math.min(events().length, state.cursor + delta))
      notify()
    },
    backToLive() {
      state.mode = 'live'
      state.playing = true
      state.dismissedEnd = true
      state.cursor = events().length
      notify()
    },
    replayFromStart() {
      state.mode = 'replay'
      state.playing = true
      state.cursor = 0
      state.dismissedEnd = true
      notify()
    },
    setSpeed(value) {
      state.speed = Number(value) || 1
    },
    dismissEnd() {
      state.dismissedEnd = true
      notify()
    },
  }
}
