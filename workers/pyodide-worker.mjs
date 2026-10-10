import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parentPort } from 'node:worker_threads'
import { loadPyodide } from 'pyodide'

const OUTPUT_LIMIT_BYTES = 64 * 1024
const TAIL_CHARS = 2000

const INDEX_URL = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  'node_modules',
  'pyodide',
)

const JS_GLOBALS = Object.freeze({})

const HARDEN = `
import sys as _sys
import os as _os
for _name in ('js', 'pyodide_js', 'micropip', 'pyodide.http', 'pyodide.webloop', 'pyodide.console'):
    _sys.modules.pop(_name, None)
try:
    _os.environ.clear()
except Exception:
    pass
`

const HARNESS = `
import io as _io
import json as _json
import sys as _sys
import traceback as _traceback
from contextlib import redirect_stdout as _redirect_stdout
from contextlib import redirect_stderr as _redirect_stderr

_META = _json.loads(_META_LITERAL)
_CODE = _META['code']
_TESTS = _META['tests'] or []
_OUTPUT_LIMIT = _META['outputLimitBytes']

class _Abort(Exception):
    pass

def _norm(text):
    return (text or '').strip()

def _has_main_block(source):
    try:
        import ast as _ast
        _tree = _ast.parse(source)
    except Exception:
        return False
    for _node in _ast.walk(_tree):
        if isinstance(_node, _ast.If):
            _test = _ast.dump(_node.test)
            if '__name__' in _test and '__main__' in _test:
                return True
    return False

def _execute_case(source, case):
    namespace = {'__name__': '__main__'}
    self_driving = _has_main_block(source)
    out = _io.StringIO()
    err = _io.StringIO()
    saved_stdin = _sys.stdin
    _sys.stdin = _io.StringIO(case.get('input') or '')
    try:
        with _redirect_stdout(out), _redirect_stderr(err):
            exec(compile(source, '<submission>', 'exec'), namespace)
            entry = namespace.get('solve')
            if callable(entry) and not self_driving:
                arity = 0
                try:
                    import inspect as _inspect
                    arity = len(_inspect.signature(entry).parameters)
                except Exception:
                    arity = 1
                value = entry(case.get('input') or '') if arity >= 1 else entry()
                if value is not None:
                    _sys.stdout.write(value if isinstance(value, str) else (repr(value) + '\\n'))
    finally:
        _sys.stdin = saved_stdin
    return out.getvalue(), err.getvalue()

def _main():
    chunks = []
    results = []
    used = 0
    for _case in _TESTS:
        try:
            _out, _err = _execute_case(_CODE, _case)
        except BaseException:
            _out, _err = '', _traceback.format_exc()
        used += len(_out) + len(_err)
        if used > _OUTPUT_LIMIT:
            raise _Abort('沙箱输出超过 ' + str(_OUTPUT_LIMIT) + ' 字节上限，已中止执行')
        chunks.append(_out)
        if _err:
            chunks.append('[stderr]\\n' + _err)
        results.append(_norm(_out) == _norm(_case.get('expected') or ''))
    return {
        'passed': sum(1 for _ok in results if _ok),
        'total': len(results),
        'stdout': ''.join(chunks),
        'error': None,
    }

_json.dumps(_main())
`

function buildRunner(code, tests) {
  const metaLiteral = JSON.stringify(
    JSON.stringify({ code, tests, outputLimitBytes: OUTPUT_LIMIT_BYTES }),
  )
  return HARDEN + '\n' + HARNESS.replace('_META_LITERAL', metaLiteral)
}

function tail(text) {
  const value = String(text || '')
  return value.length <= TAIL_CHARS ? value : value.slice(value.length - TAIL_CHARS)
}

function totalOf(tests) {
  return Array.isArray(tests) ? tests.length : 0
}

async function execute(message) {
  let outputBytes = 0
  let outputExceeded = false
  let collected = ''

  const collect = (chunk) => {
    const text = String(chunk || '')
    outputBytes += text.length
    collected += text
    if (outputBytes > OUTPUT_LIMIT_BYTES) {
      outputExceeded = true
      throw new Error('sandbox output limit exceeded')
    }
  }

  const pyodide = await loadPyodide({
    indexURL: INDEX_URL,
    jsglobals: JS_GLOBALS,
  })

  parentPort.postMessage({ type: 'ready' })

  pyodide.setStdout({
    batched: (chunk) => {
      collect(chunk)
    },
  })
  pyodide.setStderr({
    batched: (chunk) => {
      collect(chunk)
    },
  })

  const runStartedAt = Date.now()
  try {
    const raw = await pyodide.runPythonAsync(buildRunner(message.code, message.tests))
    const payload = JSON.parse(String(raw))
    parentPort.postMessage({
      type: 'result',
      status: outputExceeded ? 'error' : 'ok',
      passed: Number(payload.passed) || 0,
      total: Number(payload.total) || totalOf(message.tests),
      timeMs: Date.now() - runStartedAt,
      stdoutTail: tail(String(payload.stdout || '') + collected),
      ...(outputExceeded
        ? { errorMessage: `沙箱输出超过 ${OUTPUT_LIMIT_BYTES} 字节上限，已中止执行` }
        : {}),
    })
  } catch (error) {
    parentPort.postMessage({
      type: 'result',
      status: 'error',
      passed: 0,
      total: totalOf(message.tests),
      timeMs: Date.now() - runStartedAt,
      stdoutTail: tail(collected),
      errorMessage: outputExceeded
        ? `沙箱输出超过 ${OUTPUT_LIMIT_BYTES} 字节上限，已中止执行`
        : error instanceof Error
          ? error.message
          : String(error),
    })
  }
}

if (parentPort) {
  parentPort.on('message', (message) => {
    if (!message || message.type !== 'run') return
    execute(message).catch((error) => {
      parentPort.postMessage({
        type: 'result',
        status: 'error',
        passed: 0,
        total: totalOf(message?.tests),
        timeMs: 0,
        stdoutTail: '',
        errorMessage: error instanceof Error ? error.message : String(error),
      })
    })
  })
}
