import { DatabaseSync } from 'node:sqlite'
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export type Db = DatabaseSync

function schemaText(): string {
  return readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'schema.sql'), 'utf8')
}

export function openDb(file: string): Db {
  mkdirSync(dirname(file), { recursive: true })
  const handle = new DatabaseSync(file)
  handle.exec('PRAGMA journal_mode=WAL;')
  handle.exec(schemaText())
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
