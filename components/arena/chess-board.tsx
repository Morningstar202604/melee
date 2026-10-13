'use client'

import { Chess } from 'chess.js'
import { cn } from '@/lib/cn'

export interface ChessBoardProps {
  fen: string
  lastMove?: { from: string; to: string } | null
  compact?: boolean
}

const FILES = 'abcdefgh'
const PIECE_GLYPH: Record<string, string> = {
  wp: '♙',
  wn: '♘',
  wb: '♗',
  wr: '♖',
  wq: '♕',
  wk: '♔',
  bp: '♟',
  bn: '♞',
  bb: '♝',
  br: '♜',
  bq: '♛',
  bk: '♚',
}

export function ChessBoard({ fen, lastMove, compact = false }: ChessBoardProps) {
  let board: ReturnType<Chess['board']>
  try {
    board = new Chess(fen).board()
  } catch {
    return (
      <p className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 font-mono text-xs text-bad">
        无法解析的 FEN：{fen}
      </p>
    )
  }

  const highlight = new Set<string>()
  if (lastMove) {
    highlight.add(lastMove.from)
    highlight.add(lastMove.to)
  }

  return (
    <div className={cn('select-none', compact ? 'max-w-56' : 'max-w-105')}>
      <div
        role="img"
        aria-label={`国际象棋局面 ${fen}`}
        className="grid aspect-square w-full grid-cols-8 overflow-hidden rounded-lg border border-hairline"
      >
        {board.map((row, rowIndex) =>
          row.map((cell, fileIndex) => {
            const square = `${FILES[fileIndex]}${8 - rowIndex}`
            const dark = (rowIndex + fileIndex) % 2 === 1
            const isLast = highlight.has(square)
            return (
              <span
                key={square}
                data-square={square}
                className={cn(
                  'relative flex items-center justify-center leading-none',
                  dark ? 'bg-[#b58863]' : 'bg-[#efdfc0]',
                )}
              >
                {isLast ? (
                  <span aria-hidden className="absolute inset-0 bg-blaze/40" />
                ) : null}
                {cell ? (
                  <span
                    className={cn(
                      'relative',
                      compact ? 'text-lg' : 'text-[clamp(1.1rem,4.2vw,2rem)]',
                      cell.color === 'w' ? 'text-[#fdfaf3]' : 'text-[#1c1814]',
                    )}
                    style={{ textShadow: '0 1px 2px rgba(0,0,0,0.45)' }}
                  >
                    {PIECE_GLYPH[`${cell.color}${cell.type}`]}
                  </span>
                ) : null}
              </span>
            )
          }),
        )}
      </div>
      <div
        className={cn(
          'mt-1 flex justify-between px-0.5 font-mono text-dust',
          compact ? 'text-[9px]' : 'text-[10px]',
        )}
        aria-hidden
      >
        {FILES.split('').map((file) => (
          <span key={file}>{file}</span>
        ))}
      </div>
    </div>
  )
}
