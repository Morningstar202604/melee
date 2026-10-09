import { test } from 'node:test'
import assert from 'node:assert/strict'

test('test runner 能加载 TypeScript 测试文件', () => {
  assert.equal(1 + 1, 2)
})
