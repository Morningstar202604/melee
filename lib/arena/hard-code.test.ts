import { test } from 'node:test'
import assert from 'node:assert/strict'
import { extractCode } from './hard-code.ts'

test('extractCode 取第一个代码块', () => {
  const text = '先解释一下\n```python\nprint("a")\n```\n然后是第二个\n```python\nprint("b")\n```'
  assert.equal(extractCode(text), 'print("a")')
})

test('extractCode 对无代码块返回 null', () => {
  assert.equal(extractCode('就是一段普通文字，没有围栏'), null)
  assert.equal(extractCode(''), null)
})

test('extractCode 带语言标识的围栏也能取', () => {
  const text = '```py\nx = 1\n```'
  assert.equal(extractCode(text), 'x = 1')
})

test('extractCode 围栏未闭合时仍尽力取出内容', () => {
  const text = '一些前言\n```python\nwhile True: pass'
  assert.equal(extractCode(text), 'while True: pass')
})

test('extractCode 对纯空白输入返回 null', () => {
  assert.equal(extractCode('   '), null)
})

test('extractCode 不带语言标识的围栏也能取', () => {
  const text = '```\nhello\n```'
  assert.equal(extractCode(text), 'hello')
})
