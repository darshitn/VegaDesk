import { isSessionVerified } from './apiConfig.js'

/**
 * Mutation guard utility for honest optimistic updates, rollbacks, and pending states.
 * Enforces session authorization: blocks writes if the backend session is unverified.
 */
export function createMutationGuard(options = {}) {
  const pendingKeys = new Set()
  const requireSession = options.requireSession !== undefined ? options.requireSession : (typeof window !== 'undefined')
  const checkSessionReady = options.isSessionReady || (() => isSessionVerified())

  function isPending(key) {
    return pendingKeys.has(key)
  }

  function start(key) {
    if (pendingKeys.has(key)) return false
    pendingKeys.add(key)
    return true
  }

  function end(key) {
    pendingKeys.delete(key)
  }

  async function execute({
    key,
    optimisticApply,
    rollback,
    mutationFn,
    onSuccess,
    onError,
  }) {
    // 1. Session verification authorization gate
    if (requireSession && !checkSessionReady()) {
      const err = new Error('Mutation blocked: backend session is not verified or profile mismatched.')
      rollback?.()
      onError?.(err)
      return { success: false, blocked: true, error: err }
    }

    if (key && !start(key)) {
      return { skipped: true }
    }

    try {
      optimisticApply?.()
      const result = await mutationFn()
      onSuccess?.(result)
      return { success: true, result }
    } catch (err) {
      rollback?.()
      onError?.(err)
      return { success: false, error: err }
    } finally {
      if (key) end(key)
    }
  }

  return {
    isPending,
    start,
    end,
    execute,
  }
}
