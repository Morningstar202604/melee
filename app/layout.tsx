import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import './globals.css'

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
  display: 'swap',
})

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'melee · AI 大乱斗',
  description: '多个大模型同场竞技的 AI 擂台 — 实时对比、公平裁判、观众投票决定胜负',
  keywords: ['AI', 'LLM', '大模型', '对战', '评测', 'Arena', 'Chatbot'],
}

type LayoutParams = { children: React.ReactNode }

export default function RootLayout({ children }: LayoutParams) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  )
}
