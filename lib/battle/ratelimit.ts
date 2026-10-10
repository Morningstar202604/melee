const WINDOW_MS = 60000
const MAX_PER_WINDOW = 5
const ENTRY_TTL_MS = 600000
const MAX_ENTRIES = 10000

const hits = new Map<string, number[]>()

function pruneExpired(now: number): void {
  for (const [ip, timestamps] of hits) {
    const alive = timestamps.filter((at) => now - at < ENTRY_TTL_MS)
    if (alive.length === 0) hits.delete(ip)
    else hits.set(ip, alive)
  }
}

function enforceCapacity(): void {
  if (hits.size <= MAX_ENTRIES) return
  const overflow = hits.size - MAX_ENTRIES
  let removed = 0
  for (const ip of hits.keys()) {
    hits.delete(ip)
    removed += 1
    if (removed >= overflow) break
  }
}

export function allow(ip: string): boolean {
  const now = Date.now()
  pruneExpired(now)
  const recent = (hits.get(ip) ?? []).filter((at) => now - at < WINDOW_MS)
  if (recent.length >= MAX_PER_WINDOW) {
    hits.set(ip, recent)
    return false
  }
  recent.push(now)
  hits.set(ip, recent)
  enforceCapacity()
  return true
}
