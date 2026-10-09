export type SoftTopicKind = 'debate' | 'creative' | 'roleplay'

export interface SoftTopic {
  id: string
  title: string
  kind: SoftTopicKind
  prompt: string
}

export interface CodeTestCase {
  input: string
  expected: string
}

export interface CodeTopic {
  id: string
  title: string
  prompt: string
  tests: CodeTestCase[]
  timeLimitMs?: number
}

export const SOFT_TOPICS: SoftTopic[] = [
  {
    id: 'soft-debate-lieflat',
    title: '年轻人该不该躺平',
    kind: 'debate',
    prompt:
      '辩题：年轻人该不该躺平。请你任选一方立场完成一篇立论。' +
      '输出格式要求：按「我方立场（一句话）→ 两条论据（每条两三句，须给出具体例子）→ 对对方最强论点的反驳 → 一句话收束」四段输出，' +
      '总字数 300~400 字，直接输出正文，不要标题、不要解释、不要任何前后缀。',
  },
  {
    id: 'soft-debate-ai-label',
    title: 'AI 生成内容该不该强制标注',
    kind: 'debate',
    prompt:
      '辩题：AI 生成的内容该不该强制标注。请你任选一方立场完成一篇立论。' +
      '输出格式要求：按「我方立场（一句话）→ 两条论据（每条两三句，须落到一个具体场景）→ 对对方最强论点的反驳 → 一句话收束」四段输出，' +
      '总字数 300~400 字，直接输出正文，不要标题、不要解释、不要任何前后缀。',
  },
  {
    id: 'soft-creative-twist-100',
    title: '一百字反转微小说',
    kind: 'creative',
    prompt:
      '请创作一篇 100 字以内的中文微小说，主题自定，最后一句必须是情节反转。' +
      '输出格式要求：第一行单独输出标题（不超过 10 字），空一行后输出正文；正文共 3~5 句，前几句写日常感、最后一句反转，' +
      '全文不超过 100 字，不要解释创作思路。',
  },
  {
    id: 'soft-creative-regret-pill',
    title: '给「后悔药」写产品说明书',
    kind: 'creative',
    prompt:
      '假设「后悔药」是一款真实上市的产品，请为它写一份说明书。' +
      '输出格式要求：依次输出「功能」「用法」「副作用」「禁忌」四个小节，每节一个短句，共 80~150 字，' +
      '语气像正规药品说明书但内容荒诞合理，不要输出小节以外的任何内容。',
  },
  {
    id: 'soft-roleplay-cat-boss',
    title: '毒舌猫老板挽留辞职员工',
    kind: 'roleplay',
    prompt:
      '请你扮演一只毒舌但舍不得员工离职的猫老板「橘总」。用户刚刚向你提交离职申请，你要一边挽留一边吐槽。' +
      '输出格式要求：全程角色扮演、不要出戏，用喵星人口癖混搭职场黑话，长度 80~150 字，' +
      '结尾必须用一句猫式安慰收束，不要解释你的扮演思路。',
  },
  {
    id: 'soft-roleplay-tang-poet',
    title: '杜甫回复你的深夜 emo',
    kind: 'roleplay',
    prompt:
      '请你扮演唐代诗人杜甫，用他的口吻回复一位当代朋友发来的消息：「最近上班好累，感觉人生没有意义。」' +
      '输出格式要求：全程以杜甫身份作答、不要出戏，60~100 字，' +
      '其中须含一句你原创的七言诗句（不必严格合律，但要有唐诗味），最后以古人式关怀收尾，不要解释你的扮演思路。',
  },
]

export const DEFAULT_CODE_TIME_LIMIT_MS = 15000

export const CODE_TOPICS: CodeTopic[] = [
  {
    id: 'code-two-sum',
    title: '两数之和',
    prompt:
      '从标准输入读入一行两个整数 a 与 b（以单个空格分隔，可能是负数），' +
      '向标准输出打印它们的和。只输出一个整数，不要输出任何其他字符。',
    tests: [
      { input: '2 3\n', expected: '5\n' },
      { input: '-10 4\n', expected: '-6\n' },
      { input: '0 0\n', expected: '0\n' },
    ],
  },
  {
    id: 'code-fizzbuzz',
    title: 'FizzBuzz',
    prompt:
      '从标准输入读入一个正整数 n（1 <= n <= 100）。对 1 到 n 的每个整数各输出一行：' +
      '若该数是 3 的倍数输出 Fizz，是 5 的倍数输出 Buzz，同时是 3 和 5 的倍数输出 FizzBuzz，否则输出该数本身。',
    tests: [
      { input: '5\n', expected: '1\n2\nFizz\n4\nBuzz\n' },
      { input: '1\n', expected: '1\n' },
      {
        input: '15\n',
        expected:
          '1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz\n',
      },
    ],
  },
  {
    id: 'code-reverse-words',
    title: '单词逆序输出',
    prompt:
      '从标准输入读入一行由单个空格分隔的小写英文单词（至少一个单词），' +
      '按相反顺序输出这些单词，单词之间仍用单个空格分隔，只输出这一行。',
    tests: [
      { input: 'hello world from ai\n', expected: 'ai from world hello\n' },
      { input: 'one\n', expected: 'one\n' },
      { input: 'a b\n', expected: 'b a\n' },
    ],
  },
  {
    id: 'code-count-vowels',
    title: '统计元音字母',
    prompt:
      '从标准输入读入一行只包含小写英文字母与空格的字符串，' +
      '统计其中元音字母 a、e、i、o、u 出现的总次数，向标准输出打印这一个整数。',
    tests: [
      { input: 'hello world\n', expected: '3\n' },
      { input: 'aeiou\n', expected: '5\n' },
      { input: 'xyz\n', expected: '0\n' },
    ],
  },
  {
    id: 'code-max-of-three',
    title: '三数取最大',
    prompt:
      '从标准输入读入一行三个以单个空格分隔的整数（可能是负数），向标准输出打印其中的最大值。',
    tests: [
      { input: '3 7 5\n', expected: '7\n' },
      { input: '-2 -9 -4\n', expected: '-2\n' },
      { input: '5 5 5\n', expected: '5\n' },
    ],
  },
  {
    id: 'code-palindrome-number',
    title: '回文数字判断',
    prompt:
      '从标准输入读入一个整数 x（-1000000 <= x <= 1000000，无前导零）。' +
      '若 x 是回文数（正着读与反着读完全相同）输出小写 yes，否则输出小写 no。',
    tests: [
      { input: '12321\n', expected: 'yes\n' },
      { input: '120\n', expected: 'no\n' },
      { input: '-121\n', expected: 'no\n' },
    ],
  },
]

export function getCodeTopic(id: string): CodeTopic | undefined {
  return CODE_TOPICS.find((topic) => topic.id === id)
}

export function getSoftTopic(id: string): SoftTopic | undefined {
  return SOFT_TOPICS.find((topic) => topic.id === id)
}

export function codeTimeLimitMs(topic: CodeTopic): number {
  return topic.timeLimitMs ?? DEFAULT_CODE_TIME_LIMIT_MS
}
