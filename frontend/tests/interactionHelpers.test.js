import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createHealthPoller } from '../src/lib/healthPoller.js'
import { createFreshnessTracker } from '../src/lib/freshnessTracker.js'
import { createMutationGuard } from '../src/lib/mutationGuard.js'

// ── Health Poller Tests ──────────────────────────────────────────

test('healthPoller: reports ok status and schedules onlineInterval on healthy response', async () => {
  let statusReported = null
  let scheduledDelay = null

  const mockFetch = async () => ({
    ok: true,
    json: async () => ({ status: 'ok', provider_status: 'ready' }),
  })

  const poller = createHealthPoller({
    fetchFn: mockFetch,
    endpoint: 'http://localhost:8000/health',
    onStatusChange: (s) => { statusReported = s },
    onlineIntervalMs: 15000,
    retryIntervalMs: 3000,
  })

  // Wait a microtick for initial check
  await new Promise((r) => setTimeout(r, 10))
  assert.equal(statusReported, 'ok')

  poller.stop()
})

test('healthPoller: reports degraded and retries quickly on malformed payload', async () => {
  let statusReported = null

  // Payload without status field
  const mockFetch = async () => ({
    ok: true,
    json: async () => ({ some_random_key: 123 }),
  })

  const poller = createHealthPoller({
    fetchFn: mockFetch,
    onStatusChange: (s) => { statusReported = s },
    retryIntervalMs: 100,
  })

  await new Promise((r) => setTimeout(r, 10))
  assert.equal(statusReported, 'degraded')

  poller.stop()
})

test('healthPoller: reports Offline on network failure / 5xx', async () => {
  let statusReported = null

  const mockFetch = async () => ({
    ok: false,
    status: 503,
  })

  const poller = createHealthPoller({
    fetchFn: mockFetch,
    onStatusChange: (s) => { statusReported = s },
    retryIntervalMs: 100,
  })

  await new Promise((r) => setTimeout(r, 10))
  assert.equal(statusReported, 'Offline')

  poller.stop()
})

test('healthPoller: stop() prevents further callbacks and cleans up', async () => {
  let callCount = 0

  const mockFetch = async () => {
    callCount++
    return {
      ok: true,
      json: async () => ({ status: 'ok' }),
    }
  }

  const poller = createHealthPoller({
    fetchFn: mockFetch,
    onlineIntervalMs: 20,
    onStatusChange: () => {},
  })

  await new Promise((r) => setTimeout(r, 5))
  poller.stop()

  const snapshot = callCount
  await new Promise((r) => setTimeout(r, 50))
  assert.equal(callCount, snapshot, 'No new fetches after stop()')
})

// ── Freshness Tracker Tests ──────────────────────────────────────

test('freshnessTracker: tracks loading, success, and updates timestamp', () => {
  const tracker = createFreshnessTracker()

  const seq = tracker.startRequest('tasks')
  assert.equal(seq, 1)
  assert.equal(tracker.getState('tasks').loading, true)
  assert.equal(tracker.getState('tasks').isStale, false)

  const ok = tracker.completeSuccess('tasks', seq)
  assert.equal(ok, true)
  const st = tracker.getState('tasks')
  assert.equal(st.loading, false)
  assert.equal(st.isStale, false)
  assert.equal(typeof st.lastRefreshed, 'number')
})

test('freshnessTracker: rejects out-of-order stale responses', () => {
  const tracker = createFreshnessTracker()

  const seq1 = tracker.startRequest('tasks') // seq 1
  const seq2 = tracker.startRequest('tasks') // seq 2 supersedes seq 1

  // seq 2 resolves first
  const ok2 = tracker.completeSuccess('tasks', seq2)
  assert.equal(ok2, true)

  // seq 1 arrives late (network race)
  const ok1 = tracker.completeSuccess('tasks', seq1)
  assert.equal(ok1, false, 'Late response must be rejected')

  // seq 1 failure also rejected
  const fail1 = tracker.completeFailure('tasks', seq1, 'Stale error')
  assert.equal(fail1, false)
  assert.equal(tracker.getState('tasks').isStale, false)
})

test('freshnessTracker: failure preserves cached state and marks isStale', () => {
  const tracker = createFreshnessTracker()

  // First request succeeds
  const s1 = tracker.startRequest('projects')
  tracker.completeSuccess('projects', s1)
  const lastGoodTime = tracker.getState('projects').lastRefreshed

  // Second request fails
  const s2 = tracker.startRequest('projects')
  const failed = tracker.completeFailure('projects', s2, 'Server 500')
  assert.equal(failed, true)

  const st = tracker.getState('projects')
  assert.equal(st.loading, false)
  assert.equal(st.isStale, true)
  assert.equal(st.error, 'Server 500')
  assert.equal(st.lastRefreshed, lastGoodTime, 'Cached timestamp preserved')

  // Recovery succeeds
  const s3 = tracker.startRequest('projects')
  tracker.completeSuccess('projects', s3)
  assert.equal(tracker.getState('projects').isStale, false)
  assert.equal(tracker.getState('projects').error, null)
})

// ── Mutation Guard Tests ─────────────────────────────────────────

test('mutationGuard: executes mutation and prevents duplicate submissions while pending', async () => {
  const guard = createMutationGuard()
  let runs = 0

  const promise1 = guard.execute({
    key: 'delete-123',
    mutationFn: async () => {
      runs++
      await new Promise((r) => setTimeout(r, 20))
      return 'done'
    },
  })

  // Concurrent second submit with same key
  const res2 = await guard.execute({
    key: 'delete-123',
    mutationFn: async () => {
      runs++
      return 'done'
    },
  })

  assert.equal(res2.skipped, true, 'Second submit was blocked')
  const res1 = await promise1
  assert.equal(res1.success, true)
  assert.equal(runs, 1, 'Only one mutation execution ran')
  assert.equal(guard.isPending('delete-123'), false, 'Key released after completion')
})

test('mutationGuard: handles ambiguous write timeout safely with rollback', async () => {
  const guard = createMutationGuard()
  let rolledBack = false
  let state = ['task-1']

  const res = await guard.execute({
    key: 'delete-task-1',
    optimisticApply: () => { state = [] },
    rollback: () => {
      rolledBack = true
      state = ['task-1']
    },
    mutationFn: async () => {
      // Simulating network abort / request timeout
      const err = new Error('The operation was aborted')
      err.name = 'AbortError'
      throw err
    },
  })

  assert.equal(res.success, false)
  assert.equal(rolledBack, true, 'Rollback must execute on timeout')
  assert.deepEqual(state, ['task-1'], 'State restored to previous snapshot')
  assert.equal(guard.isPending('delete-task-1'), false, 'Pending key released after timeout')
})

test('mutationGuard: preserves entered form input on write failure', async () => {
  let formInput = 'Draft a report'
  const guard = createMutationGuard()

  const res = await guard.execute({
    key: 'add-task-draft',
    mutationFn: async () => {
      throw new Error('HTTP 503')
    },
    onSuccess: () => {
      // Only cleared on confirmed success!
      formInput = ''
    },
  })

  assert.equal(res.success, false)
  assert.equal(formInput, 'Draft a report', 'User-typed form input preserved on mutation failure')
})

// ── Onboarding & SetupWizard Contracts ───────────────────────────
import { DEFAULT_SETUP_CONFIG, persistSetupChoices } from '../src/lib/setupConfig.js'

test('SetupWizard: defaults to deterministic-only offline mode (none) at ₹0', () => {
  assert.equal(DEFAULT_SETUP_CONFIG.llm, 'none')
})

test('SetupWizard: persists deterministic offline choice truthfully to storage', () => {
  const fakeStore = {}
  const storage = {
    setItem: (k, v) => { fakeStore[k] = String(v) },
    removeItem: (k) => { delete fakeStore[k] },
  }

  persistSetupChoices(storage, { ...DEFAULT_SETUP_CONFIG, llm: 'none' })
  assert.equal(fakeStore['jarvisSetupComplete'], 'true')
  assert.equal(fakeStore['jarvisLlmProvider'], 'none')
})

test('SetupWizard: explicit cloud opt-in is saved and server default removes override', () => {
  const fakeStore = {}
  const storage = {
    setItem: (k, v) => { fakeStore[k] = String(v) },
    removeItem: (k) => { delete fakeStore[k] },
  }

  // Explicit Gemini opt-in
  persistSetupChoices(storage, { ...DEFAULT_SETUP_CONFIG, llm: 'gemini' })
  assert.equal(fakeStore['jarvisLlmProvider'], 'gemini')

  // Server default (clears key so backend .env controls it)
  persistSetupChoices(storage, { ...DEFAULT_SETUP_CONFIG, llm: 'backend' })
  assert.equal(fakeStore['jarvisLlmProvider'], undefined)
})

// ── Session Authorization Gate Tests ─────────────────────────────
import { verifyBackendSession, isSessionVerified, resetSessionVerification, verifiedFetch, subscribeSession, getSessionState } from '../src/lib/apiConfig.js'

test('sessionGate: verifyBackendSession validates profile, runId, and database status', async () => {
  resetSessionVerification()

  // 1. Mismatched profile
  const mockFetchMismatch = async () => ({
    ok: true,
    json: async () => ({ profile: 'wrong_profile', database: { status: 'ready' } }),
  })
  const ok1 = await verifyBackendSession(mockFetchMismatch)
  assert.equal(ok1, false)
  assert.equal(isSessionVerified(), false)

  // 2. Database not ready
  const mockFetchDbDown = async () => ({
    ok: true,
    json: async () => ({ profile: 'default', database: { status: 'unavailable' } }),
  })
  const ok2 = await verifyBackendSession(mockFetchDbDown)
  assert.equal(ok2, false)
  assert.equal(isSessionVerified(), false)

  // 3. Successful match
  const mockFetchOk = async () => ({
    ok: true,
    json: async () => ({ profile: 'default', database: { status: 'ready' } }),
  })
  const ok3 = await verifyBackendSession(mockFetchOk)
  assert.equal(ok3, true)
  assert.equal(isSessionVerified(), true)

  resetSessionVerification()
})

test('sessionGate: beta profile requires exact profile and nonempty matching run_id', async () => {
  resetSessionVerification()

  const betaClientInfo = {
    profile: 'beta',
    isBeta: true,
    runId: 'beta-run-xyz-123',
    port: 8005,
  }

  // 1. Beta client with missing run_id fails closed
  const okMissingClientRunId = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'beta', run_id: 'beta-run-xyz-123', database: { status: 'ready' } }),
  }), { profileInfo: { profile: 'beta', isBeta: true, runId: null } })
  assert.equal(okMissingClientRunId, false, 'Missing client run_id in beta mode must fail closed')
  assert.equal(isSessionVerified(), false)

  // 2. Server response missing run_id in beta mode fails closed
  const okMissingServerRunId = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'beta', database: { status: 'ready' } }),
  }), { profileInfo: betaClientInfo })
  assert.equal(okMissingServerRunId, false, 'Server missing run_id in beta mode must fail closed')
  assert.equal(isSessionVerified(), false)

  // 3. Server run_id mismatch in beta mode fails closed
  const okMismatchServerRunId = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'beta', run_id: 'different-run-456', database: { status: 'ready' } }),
  }), { profileInfo: betaClientInfo })
  assert.equal(okMismatchServerRunId, false, 'Mismatched run_id in beta mode must fail closed')
  assert.equal(isSessionVerified(), false)

  // 4. Server profile mismatch (default profile running on beta port) fails closed
  const okProfileMismatch = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'default', run_id: 'beta-run-xyz-123', database: { status: 'ready' } }),
  }), { profileInfo: betaClientInfo })
  assert.equal(okProfileMismatch, false, 'Mismatched profile must fail closed')
  assert.equal(isSessionVerified(), false)

  // 5. Server database unavailable fails closed
  const okDbDown = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'beta', run_id: 'beta-run-xyz-123', database: { status: 'unavailable' } }),
  }), { profileInfo: betaClientInfo })
  assert.equal(okDbDown, false, 'Unavailable database in beta must fail closed')
  assert.equal(isSessionVerified(), false)

  // 6. Valid matching beta session authorizes successfully
  const okMatching = await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'beta', run_id: 'beta-run-xyz-123', database: { status: 'ready' } }),
  }), { profileInfo: betaClientInfo })
  assert.equal(okMatching, true, 'Matching beta session must verify successfully')
  assert.equal(isSessionVerified(), true)

  resetSessionVerification()
})

test('sessionGate: epoch tracking rejects out-of-order stale health responses', async () => {
  resetSessionVerification()

  let slowResolve = null
  const slowPromise = new Promise((resolve) => { slowResolve = resolve })

  // Probe 1: Slow request that was healthy initially
  const probe1Promise = verifyBackendSession(async () => {
    await slowPromise
    return {
      ok: true,
      json: async () => ({ profile: 'default', database: { status: 'ready' } }),
    }
  })

  // Probe 2: Fast request that detects backend offline/mismatched
  const probe2Promise = verifyBackendSession(async () => ({
    ok: false,
    status: 503,
  }))

  const res2 = await probe2Promise
  assert.equal(res2, false)
  assert.equal(isSessionVerified(), false, 'Probe 2 marked session unverified')

  // Now let the older Probe 1 complete
  slowResolve()
  const res1 = await probe1Promise

  assert.equal(res1, false, 'Older probe response must be dropped as stale')
  assert.equal(isSessionVerified(), false, 'Stale probe must not overwrite unverified session state')

  resetSessionVerification()
})

test('sessionGate: subscribeSession notifies subscribers of verification changes', async () => {
  resetSessionVerification()

  const transitions = []
  const unsubscribe = subscribeSession((state) => {
    transitions.push(state.verified)
  })

  // 1. Verify
  await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'default', database: { status: 'ready' } }),
  }))
  assert.equal(transitions[transitions.length - 1], true)

  // 2. Reset / offline
  resetSessionVerification()
  assert.equal(transitions[transitions.length - 1], false)

  unsubscribe()
})

test('verifiedFetch: blocks mutations when unverified, permits reads and authorized writes', async () => {
  resetSessionVerification()

  let fetchCalls = []
  const mockFetch = async (url, options = {}) => {
    fetchCalls.push({ url, method: (options.method || 'GET').toUpperCase() })
    return { ok: true, status: 200, json: async () => ({ success: true }) }
  }

  // 1. While unverified: POST mutation is blocked
  await assert.rejects(
    () => verifiedFetch('http://localhost:8000/api/tasks', { method: 'POST', fetchFn: mockFetch }),
    /Mutation blocked/
  )

  // 2. While unverified: DELETE mutation is blocked
  await assert.rejects(
    () => verifiedFetch('http://localhost:8000/api/tasks/1', { method: 'DELETE', fetchFn: mockFetch }),
    /Mutation blocked/
  )

  // 3. While unverified: PUT mutation is blocked
  await assert.rejects(
    () => verifiedFetch('http://localhost:8000/api/workspaces/1', { method: 'PUT', fetchFn: mockFetch }),
    /Mutation blocked/
  )

  // 4. While unverified: GET read request is permitted
  const getRes = await verifiedFetch('http://localhost:8000/api/tasks', { method: 'GET', fetchFn: mockFetch })
  assert.equal(getRes.ok, true)
  assert.equal(fetchCalls.length, 1)
  assert.equal(fetchCalls[0].method, 'GET')

  // 5. Verify the session
  await verifyBackendSession(async () => ({
    ok: true,
    json: async () => ({ profile: 'default', database: { status: 'ready' } }),
  }))
  assert.equal(isSessionVerified(), true)

  // 6. While verified: POST mutation is executed
  const postRes = await verifiedFetch('http://localhost:8000/api/tasks', { method: 'POST', fetchFn: mockFetch })
  assert.equal(postRes.ok, true)
  assert.equal(fetchCalls.length, 2)
  assert.equal(fetchCalls[1].method, 'POST')

  resetSessionVerification()
})

test('mutationGuard: blocks mutation when requireSession is true and session is unverified', async () => {
  let executed = false
  const guard = createMutationGuard({
    requireSession: true,
    isSessionReady: () => false,
  })

  const res = await guard.execute({
    key: 'blocked-mutation',
    mutationFn: async () => {
      executed = true
      return 'done'
    },
  })

  assert.equal(res.success, false)
  assert.equal(res.blocked, true)
  assert.equal(executed, false, 'Mutation function must not be invoked')
})

test('mutationGuard: permits mutation when requireSession is true and session is verified', async () => {
  let executed = false
  const guard = createMutationGuard({
    requireSession: true,
    isSessionReady: () => true,
  })

  const res = await guard.execute({
    key: 'allowed-mutation',
    mutationFn: async () => {
      executed = true
      return 'done'
    },
  })

  assert.equal(res.success, true)
  assert.equal(executed, true, 'Mutation function must run when session is verified')
})

test('healthPoller: production poller with Electron beta bridge verifies session and permits verified mutation', async () => {
  const originalWindow = globalThis.window
  try {
    resetSessionVerification()
    assert.equal(isSessionVerified(), false)

    globalThis.window = {
      electronAPI: {
        getProfileInfo: () => ({
          profile: 'beta',
          port: 8005,
          runId: 'beta-test-run-123',
          isBeta: true,
          betaRoot: 'C:\\fake\\betaRoot',
        }),
      },
    }

    const fetchCalls = []
    const mockFetch = async (url, options = {}) => {
      fetchCalls.push({ url, method: (options.method || 'GET').toUpperCase() })
      if (url.includes('/health')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ok',
            profile: 'beta',
            run_id: 'beta-test-run-123',
            database: { status: 'ready' },
          }),
        }
      }
      return { ok: true, status: 200, json: async () => ({ success: true }) }
    }

    let pollerStatus = null
    // Default options exactly like App.jsx
    const poller = createHealthPoller({
      fetchFn: mockFetch,
      onStatusChange: (status) => { pollerStatus = status },
      onlineIntervalMs: 15000,
      retryIntervalMs: 3000,
    })

    // Wait a tick for initial check
    await new Promise((r) => setTimeout(r, 25))

    assert.equal(pollerStatus, 'ok')
    assert.equal(isSessionVerified(), true, 'Session must be verified for matching beta run_id')
    assert.ok(fetchCalls.some((c) => c.url.includes(':8005/health')), 'Health check targeted beta port 8005')

    // Representative verifiedFetch POST succeeds
    const postRes = await verifiedFetch('http://localhost:8005/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Synthetic Beta Task' }),
      fetchFn: mockFetch,
    })
    assert.equal(postRes.ok, true)
    assert.ok(fetchCalls.some((c) => c.url.includes('/api/tasks') && c.method === 'POST'))

    poller.stop()
  } finally {
    globalThis.window = originalWindow
    resetSessionVerification()
  }
})

test('healthPoller: production poller with missing or wrong run_id denies session and blocks mutation', async () => {
  const originalWindow = globalThis.window
  try {
    resetSessionVerification()

    // 1. Missing client run_id in beta mode
    globalThis.window = {
      electronAPI: {
        getProfileInfo: () => ({
          profile: 'beta',
          port: 8005,
          runId: null, // missing!
          isBeta: true,
        }),
      },
    }

    let pollerStatus = null
    const mockFetch = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        profile: 'beta',
        run_id: 'server-run-id',
        database: { status: 'ready' },
      }),
    })

    const poller1 = createHealthPoller({
      fetchFn: mockFetch,
      onStatusChange: (status) => { pollerStatus = status },
    })

    await new Promise((r) => setTimeout(r, 25))
    assert.equal(isSessionVerified(), false, 'Missing client run_id must deny session')
    assert.equal(pollerStatus, 'degraded')

    // Mutation is blocked
    await assert.rejects(
      () => verifiedFetch('http://localhost:8005/api/tasks', { method: 'POST', fetchFn: mockFetch }),
      /Mutation blocked/
    )
    poller1.stop()

    // 2. Mismatched server run_id
    resetSessionVerification()
    globalThis.window.electronAPI.getProfileInfo = () => ({
      profile: 'beta',
      port: 8005,
      runId: 'expected-client-run-id',
      isBeta: true,
    })

    const mockFetchMismatch = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        profile: 'beta',
        run_id: 'different-server-run-id', // mismatch!
        database: { status: 'ready' },
      }),
    })

    const poller2 = createHealthPoller({
      fetchFn: mockFetchMismatch,
      onStatusChange: (status) => { pollerStatus = status },
    })

    await new Promise((r) => setTimeout(r, 25))
    assert.equal(isSessionVerified(), false, 'Mismatched server run_id must deny session')

    // Mutation is blocked
    await assert.rejects(
      () => verifiedFetch('http://localhost:8005/api/tasks', { method: 'POST', fetchFn: mockFetchMismatch }),
      /Mutation blocked/
    )
    poller2.stop()
  } finally {
    globalThis.window = originalWindow
    resetSessionVerification()
  }
})

test('healthPoller: poller stop and supersession prevent delayed response from authorizing discarded session', async () => {
  const originalWindow = globalThis.window
  try {
    resetSessionVerification()

    globalThis.window = {
      electronAPI: {
        getProfileInfo: () => ({
          profile: 'beta',
          port: 8005,
          runId: 'beta-delayed-run-789',
          isBeta: true,
        }),
      },
    }

    let resolveDelayedFetch = null
    const delayedPromise = new Promise((resolve) => {
      resolveDelayedFetch = resolve
    })

    const mockDelayedFetch = async (url) => {
      if (url.includes('/health')) {
        await delayedPromise
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ok',
            profile: 'beta',
            run_id: 'beta-delayed-run-789',
            database: { status: 'ready' },
          }),
        }
      }
      return { ok: true, status: 200, json: async () => ({ success: true }) }
    }

    // 1. Start poller — initial checkHealth starts and awaits delayedPromise
    let pollerStatus = null
    const poller = createHealthPoller({
      fetchFn: mockDelayedFetch,
      onStatusChange: (status) => { pollerStatus = status },
    })

    // Give checkHealth a moment to enter the await
    await new Promise((r) => setTimeout(r, 10))
    assert.equal(isSessionVerified(), false, 'Session must not be verified while check is in-flight')

    // 2. Stop poller while check is still in-flight
    poller.stop()

    // 3. Now let the delayed response resolve
    resolveDelayedFetch()
    await new Promise((r) => setTimeout(r, 25))

    // 4. Session must NOT have been authorized by the delayed response!
    assert.equal(isSessionVerified(), false, 'Stopped poller must not let delayed response authorize session')
    assert.equal(pollerStatus, null, 'Status callback must not be invoked after poller stop')

    // 5. Representative mutation must remain blocked
    await assert.rejects(
      () => verifiedFetch('http://localhost:8005/api/tasks', { method: 'POST', fetchFn: mockDelayedFetch }),
      /Mutation blocked/
    )
  } finally {
    globalThis.window = originalWindow
    resetSessionVerification()
  }
})

test('healthPoller: active health timeout revokes verification, blocks mutations, and recovers on healthy probe', async () => {
  const originalWindow = globalThis.window
  let unsubscribe = null
  let poller = null
  try {
    resetSessionVerification()
    assert.equal(isSessionVerified(), false)

    globalThis.window = {
      electronAPI: {
        getProfileInfo: () => ({
          profile: 'beta',
          port: 8005,
          runId: 'beta-timeout-run-123',
          isBeta: true,
          betaRoot: 'C:\\fake\\betaRoot',
        }),
      },
    }

    const sessionTransitions = []
    unsubscribe = subscribeSession((state) => {
      sessionTransitions.push({ verified: state.verified, error: state.error })
    })

    let fetchStage = 'initial-healthy'
    const mutationRequests = []

    const mockFetch = async (url, options = {}) => {
      const method = (options.method || 'GET').toUpperCase()
      if (method === 'POST') {
        mutationRequests.push({ url, body: options.body })
        return { ok: true, status: 200, json: async () => ({ success: true }) }
      }

      if (url.includes('/health')) {
        if (fetchStage === 'initial-healthy') {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              status: 'ok',
              profile: 'beta',
              run_id: 'beta-timeout-run-123',
              database: { status: 'ready' },
            }),
          }
        }
        if (fetchStage === 'timeout') {
          // Injected fetch respects AbortSignal and hangs until aborted
          return new Promise((resolve, reject) => {
            const signal = options.signal
            if (signal) {
              if (signal.aborted) {
                const err = new Error('The operation was aborted')
                err.name = 'AbortError'
                return reject(err)
              }
              signal.addEventListener('abort', () => {
                const err = new Error('The operation was aborted')
                err.name = 'AbortError'
                reject(err)
              }, { once: true })
            }
          })
        }
        if (fetchStage === 'recovery-healthy') {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              status: 'ok',
              profile: 'beta',
              run_id: 'beta-timeout-run-123',
              database: { status: 'ready' },
            }),
          }
        }
      }

      return { ok: true, status: 200, json: async () => ({}) }
    }

    let pollerStatus = null
    poller = createHealthPoller({
      fetchFn: mockFetch,
      onStatusChange: (status) => { pollerStatus = status },
      onlineIntervalMs: 15000,
      retryIntervalMs: 3000,
      timeoutMs: 40, // Bounded timeout for test speed
    })

    // 1. Initial matching response authorizes session
    await new Promise((r) => setTimeout(r, 25))
    assert.equal(pollerStatus, 'ok')
    assert.equal(isSessionVerified(), true, 'Session must be verified on initial matching health check')
    assert.equal(sessionTransitions[sessionTransitions.length - 1].verified, true)

    // Representative mutation succeeds while verified
    const postRes1 = await verifiedFetch('http://localhost:8005/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Task 1 - Initial Authorized' }),
      fetchFn: mockFetch,
    })
    assert.equal(postRes1.ok, true)
    assert.equal(mutationRequests.length, 1)

    // 2. Second injected fetch respects AbortSignal and times out
    fetchStage = 'timeout'
    const timeoutCheckPromise = poller.checkNow()
    await timeoutCheckPromise
    await new Promise((r) => setTimeout(r, 25))

    // Verification must be revoked!
    assert.equal(isSessionVerified(), false, 'Verification must be revoked on active health timeout')
    assert.equal(pollerStatus, 'Offline')
    const lastRevocation = sessionTransitions[sessionTransitions.length - 1]
    assert.equal(lastRevocation.verified, false)
    assert.match(lastRevocation.error, /timed out|timeout/i, 'Truthful timeout error reason published')

    // Mutation must be BLOCKED with zero mutation requests dispatched
    await assert.rejects(
      () => verifiedFetch('http://localhost:8005/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Task 2 - Should be blocked' }),
        fetchFn: mockFetch,
      }),
      /Mutation blocked/
    )
    assert.equal(mutationRequests.length, 1, 'Zero mutation requests dispatched during timeout')

    // 3. Later matching response restores authorization
    fetchStage = 'recovery-healthy'
    await poller.checkNow()
    await new Promise((r) => setTimeout(r, 25))

    assert.equal(pollerStatus, 'ok')
    assert.equal(isSessionVerified(), true, 'Session authorization must be restored on recovery')
    assert.equal(sessionTransitions[sessionTransitions.length - 1].verified, true)

    // Representative mutation succeeds after recovery
    const postRes2 = await verifiedFetch('http://localhost:8005/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Task 3 - Recovered' }),
      fetchFn: mockFetch,
    })
    assert.equal(postRes2.ok, true)
    assert.equal(mutationRequests.length, 2)
  } finally {
    if (poller) poller.stop()
    if (unsubscribe) unsubscribe()
    globalThis.window = originalWindow
    resetSessionVerification()
  }
})

test('healthPoller: stopped or superseded probe does not change current verified session state', async () => {
  const originalWindow = globalThis.window
  let poller = null
  try {
    resetSessionVerification()

    globalThis.window = {
      electronAPI: {
        getProfileInfo: () => ({
          profile: 'beta',
          port: 8005,
          runId: 'beta-stop-run-456',
          isBeta: true,
        }),
      },
    }

    let fetchStage = 'healthy'
    const mockFetch = async (url, options = {}) => {
      if (url.includes('/health')) {
        if (fetchStage === 'healthy') {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              status: 'ok',
              profile: 'beta',
              run_id: 'beta-stop-run-456',
              database: { status: 'ready' },
            }),
          }
        }
        if (fetchStage === 'hang') {
          return new Promise((resolve, reject) => {
            if (options.signal) {
              options.signal.addEventListener('abort', () => {
                const err = new Error('The operation was aborted')
                err.name = 'AbortError'
                reject(err)
              }, { once: true })
            }
          })
        }
      }
      return { ok: true, status: 200, json: async () => ({}) }
    }

    // 1. Establish verified session
    poller = createHealthPoller({
      fetchFn: mockFetch,
      timeoutMs: 50,
    })
    await new Promise((r) => setTimeout(r, 20))
    assert.equal(isSessionVerified(), true, 'Session initially verified')

    // 2. Start a probe that hangs, then immediately stop poller
    fetchStage = 'hang'
    const hangPromise = poller.checkNow()
    poller.stop()

    await hangPromise
    await new Promise((r) => setTimeout(r, 30))

    // 3. Stopped probe must NOT change current verified state!
    assert.equal(isSessionVerified(), true, 'Stopped probe must not change current verified state')
  } finally {
    if (poller) poller.stop()
    globalThis.window = originalWindow
    resetSessionVerification()
  }
})


