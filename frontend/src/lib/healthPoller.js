import { getApiBase, getProfileInfo, verifyBackendSession, getSessionState } from './apiConfig.js'

/**
 * Bounded health status poller with automatic retry, payload validation, and clean teardown.
 * Integrates directly with the centralized verifyBackendSession session gate.
 */
export function createHealthPoller({
  fetchFn = (typeof fetch !== 'undefined' ? fetch : null),
  endpoint = null,
  expectedProfile = null,
  onStatusChange,
  onlineIntervalMs = 15000,
  retryIntervalMs = 3000,
  timeoutMs = 3000,
} = {}) {
  let isStopped = false
  let timerId = null
  let timeoutId = null
  let currentController = null
  let currentCheckId = 0

  function resolveProfileInfo() {
    const current = getProfileInfo() || {}
    if (expectedProfile !== null && expectedProfile !== undefined) {
      return {
        ...current,
        profile: expectedProfile,
        isBeta: expectedProfile === 'beta',
      }
    }
    return { ...current }
  }

  async function checkHealth() {
    if (isStopped) return
    const checkId = ++currentCheckId

    if (currentController) {
      try { currentController.abort() } catch {}
    }
    currentController = typeof AbortController !== 'undefined' ? new AbortController() : null
    const thisController = currentController

    timeoutId = setTimeout(() => {
      try { thisController?.abort() } catch {}
    }, timeoutMs)

    try {
      const targetEndpoint = endpoint || `${getApiBase()}/health`
      const verified = await verifyBackendSession(fetchFn, {
        endpoint: targetEndpoint,
        timeoutMs,
        signal: thisController?.signal,
        isCancelled: () => isStopped || checkId !== currentCheckId,
        profileInfo: resolveProfileInfo(),
      })
      clearTimeout(timeoutId)
      if (isStopped || checkId !== currentCheckId) return

      const session = getSessionState()
      if (verified) {
        onStatusChange?.('ok', session)
        scheduleNext(onlineIntervalMs)
      } else {
        const err = session.error || ''
        const status = (err.toLowerCase().includes('offline') || err.toLowerCase().includes('timeout') || err.toLowerCase().includes('timed out') || err.includes('failed: HTTP') || err.includes('network error'))
          ? 'Offline'
          : (err.toLowerCase().includes('profile mismatch'))
            ? 'profile_mismatch'
            : 'degraded'
        onStatusChange?.(status, session)
        scheduleNext(retryIntervalMs)
      }
    } catch {
      clearTimeout(timeoutId)
      if (isStopped || checkId !== currentCheckId) return
      onStatusChange?.('Offline', getSessionState())
      scheduleNext(retryIntervalMs)
    }
  }

  function scheduleNext(delayMs) {
    if (isStopped) return
    if (timerId) clearTimeout(timerId)
    timerId = setTimeout(checkHealth, delayMs)
  }

  function stop() {
    isStopped = true
    currentCheckId++
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
    if (timerId) {
      clearTimeout(timerId)
      timerId = null
    }
    if (currentController) {
      try { currentController.abort() } catch {}
      currentController = null
    }
  }

  // Trigger initial check
  checkHealth()

  return {
    stop,
    checkNow: checkHealth,
  }
}
