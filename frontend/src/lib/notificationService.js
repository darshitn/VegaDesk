/**
 * Notification Service for V.E.G.A. (P1-E2B).
 *
 * Provides controlled notification delivery:
 * 1. OS Native notifications via Electron IPC (`show-notification`) with click-to-focus
 *    and background delivery while the overlay window is hidden.
 * 2. Browser Web Notification fallback when running in a standard web browser.
 * 3. In-app toast banner fallback when desktop notifications are unsupported, denied, or quiet.
 * 4. Deterministic deduplication by alert ID or composite key to prevent duplicate OS
 *    banners on reconnection or redundant event broadcasts.
 */

export const ALERT_KIND_LABELS = {
  timer_due: 'Timer Expired',
  reminder_due: 'Reminder',
  task_deadline: 'Task Deadline',
  focus_end: 'Focus Session Complete',
}

/**
 * Formats user-facing notification title and body from an alert object.
 */
export function formatAlertContent(alert = {}) {
  const label = ALERT_KIND_LABELS[alert?.kind] || 'Alert'
  const title = `V.E.G.A. — ${label}`
  const body = alert?.message || 'You have a notification.'
  return { title, body, label }
}

/**
 * Creates an in-memory alert deduplicator that suppresses identical alerts
 * within a sliding TTL window.
 */
export function createAlertDeduplicator({ maxEntries = 100, ttlMs = 300000 } = {}) {
  const cache = new Map() // key -> timestamp

  function getAlertKey(alert) {
    if (!alert) return ''
    if (alert.id != null) {
      return `id:${alert.id}`
    }
    const kind = alert.kind || 'alert'
    const entityId = alert.entity_id != null ? alert.entity_id : ''
    const dueOrMsg = alert.due_utc || alert.message || ''
    return `${kind}:${entityId}:${dueOrMsg}`
  }

  function prune(now) {
    for (const [key, timestamp] of cache.entries()) {
      if (now - timestamp >= ttlMs) {
        cache.delete(key)
      }
    }
    // If still over capacity, drop oldest entries
    if (cache.size > maxEntries) {
      const keysToDelete = Array.from(cache.keys()).slice(0, cache.size - maxEntries)
      for (const k of keysToDelete) cache.delete(k)
    }
  }

  return {
    getAlertKey,
    isDuplicate(alert) {
      const key = getAlertKey(alert)
      if (!key) return false
      const now = Date.now()
      const existingTime = cache.get(key)
      if (existingTime != null && now - existingTime < ttlMs) {
        return true
      }
      return false
    },
    markDelivered(alert) {
      const key = getAlertKey(alert)
      if (!key) return
      const now = Date.now()
      cache.set(key, now)
      prune(now)
    },
    clear() {
      cache.clear()
    },
    size() {
      return cache.size
    }
  }
}

/**
 * Determines current notification permission / support status.
 */
export function getNotificationPermission(env = {}) {
  const electronAPI = env.electronAPI || (typeof window !== 'undefined' ? window.electronAPI : null)
  const NotificationApi = env.NotificationApi || (typeof Notification !== 'undefined' ? Notification : null)

  // Preload presence indicates Electron native dispatch is supported, NOT that Windows Action Center
  // has granted end-user toast rendering permission. Report 'supported' to reflect capability truthfully.
  if (electronAPI && typeof electronAPI.showNotification === 'function') {
    return 'supported'
  }
  if (NotificationApi) {
    return NotificationApi.permission || 'default'
  }
  return 'unsupported'
}

/**
 * Requests notification permission from the host environment.
 */
export async function requestNotificationPermission(env = {}) {
  const electronAPI = env.electronAPI || (typeof window !== 'undefined' ? window.electronAPI : null)
  const NotificationApi = env.NotificationApi || (typeof Notification !== 'undefined' ? Notification : null)

  if (electronAPI && typeof electronAPI.showNotification === 'function') {
    return 'supported'
  }
  if (NotificationApi && typeof NotificationApi.requestPermission === 'function') {
    try {
      return await NotificationApi.requestPermission()
    } catch {
      return 'denied'
    }
  }
  return 'unsupported'
}

/**
 * Creates a notification dispatcher wired to Electron IPC, Web Notifications,
 * and an on-screen toast fallback.
 */
export function createNotificationDispatcher(options = {}) {
  const electronAPI = options.electronAPI || (typeof window !== 'undefined' ? window.electronAPI : null)
  const NotificationApi = options.NotificationApi || (typeof Notification !== 'undefined' ? Notification : null)
  const onToast = options.onToast || (() => {})
  const onNavigate = options.onNavigate || (() => {})
  const deduplicator = options.deduplicator || createAlertDeduplicator()

  let unlistenClicked = null
  if (electronAPI && typeof electronAPI.onNotificationClicked === 'function') {
    unlistenClicked = electronAPI.onNotificationClicked((data) => {
      try {
        onNavigate(data)
      } catch (err) {
        console.error('[NOTIFICATIONS] onNavigate error:', err)
      }
    })
  }

  async function dispatchAlert(alert) {
    if (!alert) {
      return { status: 'ignored', reason: 'empty_alert' }
    }

    if (deduplicator.isDuplicate(alert)) {
      return { status: 'suppressed', reason: 'duplicate', id: alert.id }
    }

    const { title, body } = formatAlertContent(alert)
    let channel = null
    let deliveryStage = 'in_app_banner'
    let observedByUser = false

    // 1. Try native Electron Notification (supports hidden overlay delivery)
    if (electronAPI && typeof electronAPI.showNotification === 'function') {
      try {
        const res = await electronAPI.showNotification({
          title,
          body,
          id: alert.id,
          kind: alert.kind,
          entity_id: alert.entity_id,
        })
        if (res && (res.accepted || res.delivered)) {
          channel = 'electron-native'
          deliveryStage = 'api_accepted'
          observedByUser = Boolean(res.observed_by_user)
        }
      } catch (err) {
        console.warn('[NOTIFICATIONS] Electron notification failed:', err)
      }
    }

    // 2. Try Web Notification fallback if not running in Electron
    if (!channel && NotificationApi) {
      try {
        if (NotificationApi.permission === 'granted') {
          new NotificationApi(title, { body })
          channel = 'browser-native'
          deliveryStage = 'api_accepted'
          observedByUser = false
        } else if (NotificationApi.permission === 'default' && typeof NotificationApi.requestPermission === 'function') {
          const perm = await NotificationApi.requestPermission().catch(() => 'denied')
          if (perm === 'granted') {
            new NotificationApi(title, { body })
            channel = 'browser-native'
            deliveryStage = 'api_accepted'
            observedByUser = false
          }
        }
      } catch (err) {
        console.warn('[NOTIFICATIONS] Web Notification failed:', err)
      }
    }

    // 3. Fallback channel tag
    if (!channel) {
      channel = 'in-app-banner'
      deliveryStage = 'in_app_banner'
    }

    // 4. Raise in-app toast for on-screen presentation
    let toastSuccess = false
    try {
      onToast({
        id: alert.id || Date.now(),
        title,
        body,
        kind: alert.kind,
        entity_id: alert.entity_id,
      })
      toastSuccess = true
    } catch (err) {
      console.error('[NOTIFICATIONS] onToast error:', err)
    }

    // If native dispatch did not accept AND in-app fallback enqueue failed:
    if (channel === 'in-app-banner' && !toastSuccess) {
      // Report failure and do NOT mark delivered in deduplicator so caller may retry
      return {
        status: 'failed',
        delivery_stage: 'failed',
        channel: 'none',
        observed_by_user: false,
        reason: 'enqueue_failed',
        id: alert.id,
        title,
        body,
      }
    }

    // Mark as delivered in deduplicator only when delivery succeeded
    deduplicator.markDelivered(alert)

    return {
      status: 'accepted',
      delivery_stage: deliveryStage,
      channel,
      observed_by_user: false,
      id: alert.id,
      title,
      body,
    }
  }

  function cleanup() {
    if (typeof unlistenClicked === 'function') {
      unlistenClicked()
      unlistenClicked = null
    }
    deduplicator.clear()
  }

  return {
    dispatchAlert,
    cleanup,
    deduplicator,
  }
}
