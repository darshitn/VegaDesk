import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { probeHealth } from '../../scripts/launcherHelpers.js'

test('beta startup probes core health; model diagnostics are a separate request', async () => {
  const paths = []
  const fakeHttp = {
    get(url, _options, callback) {
      const request = new EventEmitter()
      paths.push(new URL(url).pathname + new URL(url).search)
      queueMicrotask(() => {
        const response = new EventEmitter()
        response.statusCode = 200
        callback(response)
        response.emit('data', JSON.stringify({
          profile: 'beta', run_id: 'run-1', database: { status: 'ready' },
          provider: { status: url.includes('diagnostics') ? 'missing_model' : 'configured' },
        }))
        response.emit('end')
      })
      return request
    },
  }
  const core = await probeHealth(8005, 'run-1', 'beta', 800, fakeHttp)
  const diagnostics = await probeHealth(8005, 'run-1', 'beta', 2500, fakeHttp, true)
  assert.equal(core.ok, true)
  assert.equal(core.data.provider.status, 'configured')
  assert.equal(diagnostics.ok, true)
  assert.equal(diagnostics.data.provider.status, 'missing_model')
  assert.deepEqual(paths, ['/health', '/health?diagnostics=1'])
})
