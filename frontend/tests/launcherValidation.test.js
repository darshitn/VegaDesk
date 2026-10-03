import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import net from 'node:net'
import {
  checkPortAvailable,
  probeHealth,
  resolvePythonExecutable,
  getBetaPaths,
  getBetaEnvironment,
  killProcessTree,
} from '../../scripts/launcherHelpers.js'

test('launcher: checkPortAvailable detects occupied vs free port', async () => {
  const dummyServer = net.createServer()
  await new Promise((resolve) => dummyServer.listen(0, '127.0.0.1', resolve))
  const occupiedPort = dummyServer.address().port

  // Occupied port returns false
  const avail1 = await checkPortAvailable(occupiedPort)
  assert.equal(avail1, false, 'Occupied port must return false')

  // After close, port returns true
  await new Promise((resolve) => dummyServer.close(resolve))
  const avail2 = await checkPortAvailable(occupiedPort)
  assert.equal(avail2, true, 'Freed port must return true')
})

test('launcher: probeHealth validates matching profile, run_id, and db readiness', async () => {
  let serverResponse = {
    statusCode: 200,
    body: JSON.stringify({
      profile: 'beta',
      run_id: 'expected-run-123',
      database: { status: 'ready' },
    }),
  }

  const dummyHttp = http.createServer((req, res) => {
    res.writeHead(serverResponse.statusCode, { 'Content-Type': 'application/json' })
    res.end(serverResponse.body)
  })

  await new Promise((resolve) => dummyHttp.listen(0, '127.0.0.1', resolve))
  const port = dummyHttp.address().port

  try {
    // 1. Success matching expected run_id
    const p1 = await probeHealth(port, 'expected-run-123')
    assert.equal(p1.ok, true)
    assert.equal(p1.data.profile, 'beta')

    // 2. Mismatched run_id (wrong run from previous session holding port)
    const p2 = await probeHealth(port, 'different-run-456')
    assert.equal(p2.ok, false)
    assert.ok(p2.reason.includes('mismatched run_id'))

    // 3. Mismatched profile (e.g. default profile running on port)
    serverResponse.body = JSON.stringify({
      profile: 'default',
      run_id: 'expected-run-123',
      database: { status: 'ready' },
    })
    const p3 = await probeHealth(port, 'expected-run-123')
    assert.equal(p3.ok, false)
    assert.ok(p3.reason.includes('mismatched profile'))

    // 4. Database unready / degraded
    serverResponse.body = JSON.stringify({
      profile: 'beta',
      run_id: 'expected-run-123',
      database: { status: 'unavailable' },
    })
    const p4 = await probeHealth(port, 'expected-run-123')
    assert.equal(p4.ok, false)
    assert.ok(p4.reason.includes('database not ready'))

    // 5. HTTP 500 error
    serverResponse.statusCode = 500
    serverResponse.body = 'Internal Error'
    const p5 = await probeHealth(port, 'expected-run-123')
    assert.equal(p5.ok, false)
    assert.ok(p5.reason.includes('HTTP 500'))

    // 6. Malformed JSON
    serverResponse.statusCode = 200
    serverResponse.body = '{ not valid json'
    const p6 = await probeHealth(port, 'expected-run-123')
    assert.equal(p6.ok, false)
    assert.ok(p6.reason.includes('malformed JSON'))

  } finally {
    await new Promise((resolve) => dummyHttp.close(resolve))
  }
})

test('launcher: resolvePythonExecutable resolves working Python and handles missing interpreter', () => {
  // 1. Injected mock that succeeds
  const mockExecSuccess = (cmd, args) => {
    if (args && args.includes('import sys; print(sys.executable)')) {
      return 'C:\\Mock\\Python3\\python.exe\n'
    }
    throw new Error('Not found')
  }
  const py1 = resolvePythonExecutable({ execFileSync: mockExecSuccess })
  assert.equal(py1, 'C:\\Mock\\Python3\\python.exe')

  // 2. Injected mock that always throws
  const mockExecFail = () => {
    throw new Error('Command not found')
  }
  const py2 = resolvePythonExecutable({ execFileSync: mockExecFail, env: {} })
  assert.equal(py2, null, 'Must return null when no python candidate succeeds')
})

test('launcher: getBetaEnvironment strips cloud credentials and sets offline deterministic flags', () => {
  const baseEnv = {
    PATH: '/bin:/usr/bin',
    GEMINI_API_KEY: 'secret-gemini-key',
    OPENAI_API_KEY: 'secret-openai-key',
    ANTHROPIC_API_KEY: 'secret-anthropic-key',
  }
  const { env, runId, betaPort, betaRoot, betaDbPath } = getBetaEnvironment(
    'C:\\TestRoot\\Beta',
    'test-run-abc',
    baseEnv
  )

  assert.equal(env.VEGA_PROFILE, 'beta')
  assert.equal(env.VEGA_PORT, '8005')
  assert.equal(env.VEGA_RUN_ID, 'test-run-abc')
  assert.equal(env.LLM_PROVIDER, 'none')
  assert.equal(env.VEGA_DISABLE_VOICE, '1')
  assert.equal(env.VEGA_DISABLE_RADAR, '1')
  assert.equal(env.GEMINI_API_KEY, undefined, 'Must strip GEMINI_API_KEY')
  assert.equal(env.OPENAI_API_KEY, undefined, 'Must strip OPENAI_API_KEY')
  assert.equal(env.ANTHROPIC_API_KEY, undefined, 'Must strip ANTHROPIC_API_KEY')
  assert.equal(betaPort, 8005)
  assert.equal(betaRoot, 'C:\\TestRoot\\Beta')
  assert.ok(betaDbPath.includes('jarvis-beta.db'))
})

test('launcher: killProcessTree invokes taskkill on Windows with child PID', () => {
  let taskkillArgs = null
  const mockExecFileSync = (cmd, args) => {
    if (cmd === 'taskkill') {
      taskkillArgs = args
      return
    }
    throw new Error('Unknown command')
  }

  const dummyChild = { pid: 9876, kill: () => {} }
  killProcessTree(dummyChild, mockExecFileSync)

  if (process.platform === 'win32') {
    assert.deepEqual(taskkillArgs, ['/PID', '9876', '/T', '/F'])
  }

  // Null child handled safely without throwing
  assert.doesNotThrow(() => killProcessTree(null, mockExecFileSync))
  assert.doesNotThrow(() => killProcessTree({}, mockExecFileSync))
})
