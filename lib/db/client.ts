import { DatabaseSync } from 'node:sqlite'
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export type Db = DatabaseSync

function schemaText(): string {
  return readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'schema.sql'), 'utf8')
}

function ensureColumn(db: Db, table: string, column: string, ddl: string): void {
  const columns = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name?: unknown }>
  if (columns.some((row) => row.name === column)) return
  db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${ddl}`)
}

export function openDb(file: string): Db {
  mkdirSync(dirname(file), { recursive: true })
  const handle = new DatabaseSync(file)
  handle.exec('PRAGMA journal_mode=WAL;')
  handle.exec(schemaText())
  // 旧库迁移：CREATE TABLE IF NOT EXISTS 不会补列
  ensureColumn(handle, 'contestants', 'reasoning_text', 'TEXT')
  return handle
}

let instance: Db | null = null

export function getDb(): Db {
  if (instance) return instance
  instance = openDb(process.env.MELEE_DB ?? 'data/melee.db')
  return instance
}

export function closeDb(): void {
  if (!instance) return
  instance.close()
  instance = null
}
