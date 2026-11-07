// 管理后台：运维健康、队列、配额、赛季调度的单页视图。
// 与其他页面同一套取数/导航/i18n 底座；零构建，直接 ES module。
import { configure, el, formatTime, propagateQuery, request as apiRequest } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'
import { applyI18n, resolveLang, setLang, t } from './i18n.js'

setLang(resolveLang())
configure()
propagateQuery()
mountTopbar({ active: 'admin' })
mountFooter()
document.title = t('admin.title')
applyI18n()

let timer = null

function kv(pairs) {
  const box = document.createElement('div')
  box.className = 'kv'
  for (const [key, value] of pairs) {
    const k = document.createElement('div')
    k.className = 'k'
    k.textContent = key
    const v = document.createElement('div')
    v.textContent = value
    box.append(k, v)
  }
  return box
}

function kpi(value, label, tone = '') {
  const node = document.createElement('div')
  node.className = 'ops-kpi ' + tone
  const b = document.createElement('b')
  b.textContent = String(value)
  const span = document.createElement('span')
  span.textContent = label
  node.append(b, span)
  return node
}

function setStatus(kind, label) {
  const pill = el('overall')
  pill.className = 'status-pill ' + kind
  pill.textContent = ''
  const dot = document.createElement('span')
  dot.className = 'dot-live'
  const text = document.createElement('span')
  text.textContent = label
  pill.append(dot, text)
}

async function refresh() {
  // 每一块都独立容错：一块挂了不能拖垮整页。
  const tasks = [
    apiRequest('/health').catch(() => null),
    apiRequest('/ops/health').catch(() => null),
    apiRequest('/jobs?limit=15').catch(() => null),
    apiRequest('/quota').catch(() => null),
    apiRequest('/scheduler').catch(() => null),
    apiRequest('/stats').catch(() => null),
  ]
  const [health, ops, jobs, quota, scheduler, stats] = await Promise.all(tasks)

  // 服务健康
  let degraded = false
  if (health) {
    const checks = health.checks || {}
    const dbOk = checks.db === true
    const queueOk = checks.queue === true
    degraded = !(dbOk && queueOk)
    el('kpis').replaceChildren(
      kpi(dbOk ? 'OK' : 'DOWN', t('admin.kpi.db'), dbOk ? '' : 'bad'),
      kpi(queueOk ? 'OK' : 'DOWN', t('admin.kpi.queue'), queueOk ? '' : 'bad'),
      kpi(stats?.events ?? '—', t('admin.kpi.events')),
      kpi(stats?.viewers ?? '—', t('admin.kpi.viewers')),
    )
  } else {
    degraded = true
    el('kpis').replaceChildren(kpi('DOWN', t('admin.kpi.db'), 'bad'), kpi('DOWN', t('admin.kpi.queue'), 'bad'))
  }

  // 运维快照
  if (ops) {
    degraded = degraded || ops.status !== 'ok'
    const d = ops.details || {}
    const jobsBox = d.jobs || {}
    const backlog = d.backlog || {}
    const oldest = d.oldestPending || {}
    el('kpis').append(
      kpi(jobsBox.pending ?? 0, t('admin.kpi.pending'), (jobsBox.pending ?? 0) > (backlog.threshold ?? 20) ? 'bad' : ''),
      kpi(jobsBox.running ?? 0, t('admin.kpi.running')),
      kpi(jobsBox.failed ?? 0, t('admin.kpi.failed'), (jobsBox.failed ?? 0) > 0 ? 'warn' : ''),
      kpi(oldest.seconds ?? 0, t('admin.kpi.oldestPending')),
    )
    const problems = ops.problems || []
    const box = el('problems')
    if (problems.length) {
      box.style.display = ''
      box.textContent = problems.join(' · ')
    } else {
      box.style.display = 'none'
    }
  }

  setStatus(degraded ? 'bad' : 'ok', degraded ? t('admin.status.degraded') : t('admin.status.ok'))

  // 队列表
  const tbody = el('jobsBody')
  if (jobs && jobs.jobs && jobs.jobs.length) {
    el('jobsEmpty').style.display = 'none'
    tbody.replaceChildren(
      ...jobs.jobs.map((job) => {
        const tr = document.createElement('tr')
        const cells = [
          [job.jobId || job.id || '—', 'mono'],
          [job.kind, ''],
          [job.status, 'tag-' + job.status],
          [String(job.attempts ?? 0), ''],
          [(job.lastError || '—').slice(0, 40), ''],
        ]
        for (const [value, cls] of cells) {
          const td = document.createElement('td')
          if (cls) td.className = cls
          td.textContent = value ?? '—'
          tr.append(td)
        }
        return tr
      }),
    )
  } else {
    tbody.replaceChildren()
    el('jobsEmpty').style.display = ''
  }

  // 调度器
  if (scheduler) {
    el('schedulerBox').replaceChildren(
      kv([
        [t('admin.scheduler.enabled'), scheduler.enabled ? t('admin.scheduler.on') : t('admin.scheduler.off')],
        [t('admin.scheduler.interval'), `${scheduler.intervalSec}s`],
        [t('admin.scheduler.perTick'), scheduler.perTick],
        [t('admin.scheduler.ticks'), scheduler.ticks],
        [t('admin.scheduler.lastError'), scheduler.lastError || '—'],
        [t('admin.scheduler.seasons'), scheduler.seasonIds ? scheduler.seasonIds.join(', ') : t('admin.scheduler.auto')],
      ]),
    )
  }

  // 配额
  if (quota) {
    el('quotaBox').replaceChildren(
      kv([
        [t('admin.quota.identity'), quota.principal],
        [t('admin.quota.day'), quota.day],
        [t('admin.quota.matches'), quota.matches],
        [t('admin.quota.renders'), quota.renders],
      ]),
    )
  }

  // 站点合计
  if (stats) {
    el('siteBox').replaceChildren(
      kv([
        [t('admin.site.matches'), stats.matches ?? 0],
        [t('admin.site.finished'), stats.finished ?? 0],
        [t('admin.site.events'), stats.events ?? 0],
        [t('admin.site.live'), stats.live ?? 0],
        [t('admin.site.games'), (stats.byGame || []).length],
      ]),
    )
  }
}

el('btnRefresh').onclick = refresh
el('btnTick').onclick = async () => {
  try {
    await apiRequest('/scheduler/tick', { method: 'POST', body: {} })
    await refresh()
  } catch (error) {
    // /scheduler/tick 在 off 模式也可能被限流；失败只在控制台可见，
    // 不打断自动刷新。
    console.error('tick failed', error)
  }
}

async function boot() {
  await refresh()
  timer = setInterval(() => {
    if (!document.hidden) refresh()
  }, 5000)
}

boot()
