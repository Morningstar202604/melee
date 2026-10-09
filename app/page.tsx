import type { Metadata } from "next";
import { ArenaSetup } from "@/components/arena/arena-setup";
import { CODE_TOPICS, SOFT_TOPICS } from "@/lib/arena/topics";
import { PROVIDERS } from "@/lib/providers/registry";

export const metadata: Metadata = {
  title: "melee · AI 大乱斗",
  description: "选多个大模型 + 一个题目，实时看它们同场竞技",
};

export default function Home() {
  return (
    <div className="min-h-full bg-zinc-100 dark:bg-zinc-950">
      <header className="border-b-2 border-amber-500/70 bg-white dark:bg-zinc-900">
        <div className="mx-auto flex max-w-5xl flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-5">
          <h1 className="text-2xl font-black tracking-tight text-zinc-900 dark:text-zinc-50">
            melee · AI 大乱斗
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            选赛道 · 定题目 · 点名将 → 开打
          </p>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5 py-6">
        <ArenaSetup
          providers={PROVIDERS}
          softTopics={SOFT_TOPICS}
          codeTopics={CODE_TOPICS}
        />
      </main>
    </div>
  );
}
