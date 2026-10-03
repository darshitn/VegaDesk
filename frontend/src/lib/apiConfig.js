/**
 * Centralized API, WebSocket, and Session Authorization Gate for VEGA.
 *
 * Supports dynamic port binding and profile isolation (Normal port 8000, Beta port 8005).
 * Never hardcodes host or port in renderer components.
 *
 * Session Authorization Gate:
 * Enforces that renderer mutations and WebSockets remain unavailable until
 * the backend session identity (profile, run_id, and DB readiness) is verified.
 */

let sessionState = {
  verified: false,
  profile: null,
  runId: null,
  error: null,
  lastVerified: null,
}

let currentEpoch = 0
let latestCompletedEpoch = 0
const sessionListeners = new Set()

export function subscribeSession(callback) {
  if (typeof callback === 'function') {
    sessionListeners.add(callback)
    return () => sessionListeners.delete(callback)
  }
  return () => {}
}

function notifySessionListeners() {
  const snapshot = { ...sessionState }
  for (const listener of sessionListeners) {
    try {
      listener(snapshot)
    } catch (err) {
      console.error('[SESSION LISTENER ERROR]', err)
    }
  }
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    try {
      window.dispatchEvent(new CustomEvent('vega-session-change', { detail: snapshot }))
    } catch {}
  }
}

export function getProfileInfo() {
  if (typeof window !== 'undefined' && window.electronAPI && typeof window.electronAPI.getProfileInfo === 'function') {
    try {
      const info = window.electronAPI.getProfileInfo()
      if (info && typeof info === 'object') return info
    } catch {}
  }
  if (typeof window !== 'undefined' && window.__VEGA_PROFILE_INFO__) {
    return window.__VEGA_PROFILE_INFO__
  }
  const isBeta = typeof window !== 'undefined' && (
    window.__VEGA_PROFILE__ === 'beta' ||
    Boolean(window.location && window.location.search && window.location.search.includes('profile=beta'))
  )
  const port = typeof window !== 'undefined' && window.__VEGA_PORT__
    ? Number(window.__VEGA_PORT__)
    : (isBeta ? 8005 : 8000)
  const runId = typeof window !== 'undefined' && window.__VEGA_RUN_ID__
    ? String(window.__VEGA_RUN_ID__)
    : null

  return {
    profile: isBeta ? 'beta' : 'default',
    port,
    isBeta,
    runId,
  }
}

export function isBetaProfile() {
  const info = getProfileInfo()
  return info.profile === 'beta' || info.isBeta === true
}

export function getApiPort() {
  if (typeof window !== 'undefined') {
    if (window.__VEGA_PORT__) return Number(window.__VEGA_PORT__)
    const info = getProfileInfo()
    if (info && info.port) return Number(info.port)
  }
  return 8000
}

export function getApiBase() {
  return `http://localhost:${getApiPort()}`
}

export function getWsBase() {
  return `ws://localhost:${getApiPort()}`
}

export function getSessionState() {
  return { ...sessionState }
}

export function isSessionVerified() {
  return sessionState.verified === true
}

export function setSessionVerified(verified, details = {}) {
  sessionState = {
    verified: Boolean(verified),
    profile: details.profile || null,
    runId: details.runId || null,
    error: details.error || null,
    lastVerified: verified ? Date.now() : null,
  }
  notifySessionListeners()
}

export function resetSessionVerification() {
  // Invalidate any in-flight requests by incrementing latestCompletedEpoch
  latestCompletedEpoch = ++currentEpoch
  sessionState = {
    verified: false,
    profile: null,
    runId: null,
    error: null,
    lastVerified: null,
  }
  notifySessionListeners()
}

/**
 * Probes the backend /health endpoint and verifies:
 * 1. HTTP 200 response
 * 2. profile matches expected client profile (default or beta)
 * 3. run_id matches expected runId (strictly required for beta mode)
 * 4. database status is 'ready'
 * 5. rejects out-of-order stale health responses via sequence epoch tracking
 */
export async function verifyBackendSession(fetchFn = null, options = {}) {
  const requestEpoch = ++currentEpoch

  const isDiscarded = () => {
    if (typeof options.isCancelled === 'function' && options.isCancelled()) return true
    if (requestEpoch < latestCompletedEpoch) return true
    return false
  }

  if (isDiscarded()) {
    return false
  }

  const expected = (options && options.profileInfo) ? options.profileInfo : getProfileInfo()
  const expectedProfile = expected.profile || 'default'
  const isBeta = expected.isBeta || expectedProfile === 'beta'
  const expectedRunId = expected.runId || null
  const timeoutMs = options.timeoutMs || 3000

  // 1. Beta mode fails closed if client identity is missing
  if (isBeta && (!expectedRunId || typeof expectedRunId !== 'string' || !expectedRunId.trim())) {
    latestCompletedEpoch = requestEpoch
    setSessionVerified(false, {
      error: 'Missing client run_id in beta mode',
      profile: 'beta',
      runId: null,
    })
    return false
  }

  const doFetch = fetchFn || (
    typeof window !== 'undefined' && window.fetch
      ? window.fetch.bind(window)
      : (typeof globalThis !== 'undefined' && globalThis.fetch ? globalThis.fetch.bind(globalThis) : null)
  )

  if (!doFetch) {
    latestCompletedEpoch = requestEpoch
    setSessionVerified(false, { error: 'No fetch implementation available' })
    return false
  }

  const endpoint = options.endpoint || `${getApiBase()}/health`
  let controller = null
  let timeoutId = null

  if (typeof AbortController !== 'undefined') {
    controller = new AbortController()
    timeoutId = setTimeout(() => {
      try { controller.abort() } catch {}
    }, timeoutMs)

    if (options.signal) {
      if (options.signal.aborted) {
        try { controller.abort() } catch {}
        if (timeoutId) clearTimeout(timeoutId)
        if (isDiscarded()) return false
        latestCompletedEpoch = requestEpoch
        setSessionVerified(false, { error: 'Backend health check timed out' })
        return false
      }
      options.signal.addEventListener('abort', () => {
        try { controller.abort() } catch {}
      }, { once: true })
    }
  }

  try {
    const fetchOptions = controller ? { signal: controller.signal } : {}
    const res = await doFetch(endpoint, fetchOptions)
    if (timeoutId) clearTimeout(timeoutId)

    // Stale or discarded check
    if (isDiscarded()) {
      return false
    }

    if (!res || !res.ok) {
      latestCompletedEpoch = requestEpoch
      const err = `Backend health check failed: HTTP ${res ? res.status : 'offline'}`
      setSessionVerified(false, { error: err })
      return false
    }

    const data = await res.json()
    if (isDiscarded()) {
      return false
    }

    if (!data || typeof data !== 'object') {
      latestCompletedEpoch = requestEpoch
      setSessionVerified(false, { error: 'Malformed health payload' })
      return false
    }

    // Profile check
    if (isBeta) {
      if (data.profile !== 'beta') {
        latestCompletedEpoch = requestEpoch
        const err = `Profile mismatch: expected 'beta', got '${data.profile || 'none'}'`
        setSessionVerified(false, { error: err, profile: data.profile })
        return false
      }
    } else if (data.profile && data.profile !== expectedProfile) {
      latestCompletedEpoch = requestEpoch
      const err = `Profile mismatch: expected '${expectedProfile}', got '${data.profile}'`
      setSessionVerified(false, { error: err, profile: data.profile })
      return false
    }

    // Run ID check
    if (isBeta) {
      if (!data.run_id || data.run_id !== expectedRunId) {
        latestCompletedEpoch = requestEpoch
        const err = `Run ID mismatch: expected '${expectedRunId}', got '${data.run_id || 'none'}'`
        setSessionVerified(false, { error: err, profile: data.profile, runId: data.run_id })
        return false
      }
    } else if (expectedRunId) {
      if (!data.run_id || data.run_id !== expectedRunId) {
        latestCompletedEpoch = requestEpoch
        const err = `Run ID mismatch: expected '${expectedRunId}', got '${data.run_id || 'none'}'`
        setSessionVerified(false, { error: err, profile: data.profile, runId: data.run_id })
        return false
      }
    }

    // Database check
    if (isBeta) {
      if (!data.database || data.database.status !== 'ready') {
        latestCompletedEpoch = requestEpoch
        const err = `Database not ready: ${data?.database?.status || 'unavailable'}`
        setSessionVerified(false, { error: err, profile: data.profile, runId: data.run_id })
        return false
      }
    } else if (data.database) {
      if (data.database.status !== 'ready') {
        latestCompletedEpoch = requestEpoch
        const err = `Database not ready: ${data.database.status}`
        setSessionVerified(false, { error: err, profile: data.profile, runId: data.run_id })
        return false
      }
    } else if (data.status !== 'ok') {
      latestCompletedEpoch = requestEpoch
      const err = `Backend status not ok: ${data.status || 'malformed payload'}`
      setSessionVerified(false, { error: err })
      return false
    }

    if (isDiscarded()) {
      return false
    }

    latestCompletedEpoch = requestEpoch
    setSessionVerified(true, {
      profile: data.profile,
      runId: data.run_id,
    })
    return true
  } catch (err) {
    if (timeoutId) clearTimeout(timeoutId)
    if (isDiscarded()) {
      return false
    }
    latestCompletedEpoch = requestEpoch
    const isTimeout = (
      err?.name === 'AbortError' ||
      err?.name === 'TimeoutError' ||
      String(err?.message || '').toLowerCase().includes('abort') ||
      String(err?.message || '').toLowerCase().includes('timeout') ||
      options.signal?.aborted ||
      controller?.signal?.aborted
    )
    const errMsg = isTimeout
      ? 'Backend health check timed out'
      : (err?.message || String(err))
    setSessionVerified(false, { error: errMsg })
    return false
  }
}

/**
 * Shared verified request boundary.
 *
 * Blocks all backend mutations (POST, PUT, DELETE, PATCH) if the backend session
 * is not verified. Read requests (GET, HEAD) remain permitted per existing contract.
 */
export async function verifiedFetch(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const isMutation = ['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)

  if (isMutation && !isSessionVerified()) {
    throw new Error('Mutation blocked: backend session is not verified or profile mismatched.')
  }

  const doFetch = options.fetchFn || (
    typeof window !== 'undefined' && window.fetch
      ? window.fetch.bind(window)
      : (typeof globalThis !== 'undefined' && globalThis.fetch ? globalThis.fetch.bind(globalThis) : null)
  )

  if (!doFetch) {
    throw new Error('No fetch implementation available')
  }

  return doFetch(url, options)
}
