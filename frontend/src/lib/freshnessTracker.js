/**
 * Freshness tracker and request sequencer.
 * Prevents out-of-order responses from overwriting newer state,
 * and tracks loading, stale, and error states per dataset.
 */
const DEFAULT_STATE = { loading: false, isStale: false, error: null, lastRefreshed: null }

export function createFreshnessTracker() {
  const sequences = {}
  const state = {}

  function startRequest(dataset) {
    sequences[dataset] = (sequences[dataset] || 0) + 1
    const seq = sequences[dataset]
    state[dataset] = {
      ...DEFAULT_STATE,
      ...state[dataset],
      loading: true,
      error: null,
    }
    return seq
  }

  function completeSuccess(dataset, seq) {
    if (seq !== sequences[dataset]) {
      // Out of order: a newer request was already initiated
      return false
    }
    state[dataset] = {
      loading: false,
      isStale: false,
      error: null,
      lastRefreshed: Date.now(),
    }
    return true
  }

  function completeFailure(dataset, seq, errorMsg) {
    if (seq !== sequences[dataset]) {
      return false
    }
    state[dataset] = {
      ...state[dataset],
      loading: false,
      isStale: true,
      error: errorMsg || 'Refresh failed',
    }
    return true
  }

  function getState(dataset) {
    return { ...DEFAULT_STATE, ...state[dataset] }
  }

  return {
    startRequest,
    completeSuccess,
    completeFailure,
    getState,
  }
}
