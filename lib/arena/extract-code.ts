/** 客户端安全的代码块提取（服务端 hard-code.ts 复用同一实现，避免双份）。 */
const CODE_BLOCK_RE = /```(?:python|py)?\s*\n([\s\S]*?)```/m
const UNTERMINATED_CODE_BLOCK_RE = /```(?:python|py)?\s*\n([\s\S]*)$/m

export function extractCode(text: string): string | null {
  if (typeof text !== 'string' || text.trim().length === 0) return null
  const matched = text.match(CODE_BLOCK_RE)
  if (matched && matched[1]) return matched[1].trim()
  const fallback = text.match(UNTERMINATED_CODE_BLOCK_RE)
  if (fallback && fallback[1]) return fallback[1].trim()
  return null
}
