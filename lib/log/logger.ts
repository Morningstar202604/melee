import { redact } from './redact.ts'

type LogLevel = 'debug' | 'info' | 'warn' | 'error'

const LEVEL_ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 }

const SECRET_KEYS = new Set(['apikey', 'key', 'authorization', 'token', 'secret', 'password'])

const OPAQUE_VALUE = /^[A-Za-z0-9._-]{20,}$/

function currentLevel(): LogLevel {
  const raw = (process.env.MELEE_LOG_LEVEL ?? 'info').toLowerCase()
  return raw in LEVEL_ORDER ? (raw as LogLevel) : 'info'
}

function emit(level: LogLevel, message: string, context?: Record<string, unknown>): void {
  if (LEVEL_ORDER[level] < LEVEL_ORDER[currentLevel()]) return

  const secrets: string[] = []
  const safe: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(context ?? {})) {
    if (SECRET_KEYS.has(key.toLowerCase())) continue
    if (typeof value === 'string' && OPAQUE_VALUE.test(value)) secrets.push(value)
    safe[key] = value
  }

  const time = new Date().toISOString()
  const label = level.toUpperCase().padEnd(5)
  const safeMessage = redact(message, secrets)
  const tail = Object.keys(safe).length > 0 ? ` ${redact(JSON.stringify(safe), secrets)}` : ''
  console.log(`${time} ${label} ${safeMessage}${tail}`)
}

export const logger = {
  debug: (message: string, context?: Record<string, unknown>) => emit('debug', message, context),
  info: (message: string, context?: Record<string, unknown>) => emit('info', message, context),
  warn: (message: string, context?: Record<string, unknown>) => emit('warn', message, context),
  error: (message: string, context?: Record<string, unknown>) => emit('error', message, context),
}
