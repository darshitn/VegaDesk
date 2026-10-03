import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  formatAlertContent,
  createAlertDeduplicator,
  getNotificationPermission,
  requestNotificationPermission,
  createNotificationDispatcher,
} from '../src/lib/notificationService.js'

// ── Alert Content Formatting Tests ───────────────────────────────

test('formatAlertContent: maps known alert kinds to human-readable titles', () => {
  const kinds = [
    ['timer_due', 'V.E.G.A. — Timer Expired'],
    ['reminder_due', 'V.E.G.A. — Reminder'],
    ['task_deadline', 'V.E.G.A. — Task Deadline'],
    ['focus_end', 'V.E.G.A. — Focus Session Complete'],
    ['unknown_custom', 'V.E.G.A. — Alert'],
  ]

  for (const [kind, expectedTitle] of kinds) {
    const { title, body } = formatAlertContent({ kind, message: 'Check your oven.' })
    assert.equal(title, expectedTitle)
    assert.equal(body, 'Check your oven.')
  }
})

test('formatAlertContent: provides default message when message is absent', () => {
  const { title, body } = formatAlertContent({})
  assert.equal(title, 'V.E.G.A. — Alert')
  assert.equal(body, 'You have a notification.')
})

// ── Alert Deduplication Tests ────────────────────────────────────

test('createAlertDeduplicator: deduplicates alerts by explicit integer id', () => {
  const deduplicator = createAlertDeduplicator({ ttlMs: 10000 })
  const alert1 = { id: 42, kind: 'timer_due', message: 'Egg timer' }
  const alert2 = { id: 42, kind: 'timer_due', message: 'Egg timer' }
  const alert3 = { id: 43, kind: 'timer_due', message: 'Tea timer' }

  assert.equal(deduplicator.isDuplicate(alert1), false)
  deduplicator.markDelivered(alert1)
  assert.equal(deduplicator.isDuplicate(alert2), true)
  assert.equal(deduplicator.isDuplicate(alert3), false)
})

test('createAlertDeduplicator: deduplicates by composite key when id is missing', () => {
  const deduplicator = createAlertDeduplicator({ ttlMs: 10000 })
  const alert1 = { kind: 'reminder_due', entity_id: 'rem_1', due_utc: '2026-10-02T16:00:00Z', message: 'Stand up' }
  const alert2 = { kind: 'reminder_due', entity_id: 'rem_1', due_utc: '2026-10-02T16:00:00Z', message: 'Stand up' }
  const alert3 = { kind: 'reminder_due', entity_id: 'rem_2', due_utc: '2026-10-02T16:00:00Z', message: 'Walk' }

  assert.equal(deduplicator.isDuplicate(alert1), false)
  deduplicator.markDelivered(alert1)
  assert.equal(deduplicator.isDuplicate(alert2), true)
  assert.equal(deduplicator.isDuplicate(alert3), false)
})

test('createAlertDeduplicator: respects TTL expiry and clear()', async () => {
  const deduplicator = createAlertDeduplicator({ ttlMs: 30 })
  const alert = { id: 99, kind: 'task_deadline', message: 'Submit report' }

  assert.equal(deduplicator.isDuplicate(alert), false)
  deduplicator.markDelivered(alert)
  assert.equal(deduplicator.isDuplicate(alert), true)

  // Wait for TTL expiry
  await new Promise((r) => setTimeout(r, 45))
  assert.equal(deduplicator.isDuplicate(alert), false)

  // Clear resets immediately
  deduplicator.markDelivered(alert)
  assert.equal(deduplicator.isDuplicate(alert), true)
  deduplicator.clear()
  assert.equal(deduplicator.isDuplicate(alert), false)
  assert.equal(deduplicator.size(), 0)
})

test('createAlertDeduplicator: prunes oldest entries when capacity exceeded', () => {
  const deduplicator = createAlertDeduplicator({ maxEntries: 3, ttlMs: 100000 })

  deduplicator.markDelivered({ id: 1 })
  deduplicator.markDelivered({ id: 2 })
  deduplicator.markDelivered({ id: 3 })
  assert.equal(deduplicator.size(), 3)

  deduplicator.markDelivered({ id: 4 })
  assert.ok(deduplicator.size() <= 3)
  // Oldest entry (id: 1) should be pruned
  assert.equal(deduplicator.isDuplicate({ id: 1 }), false)
  // Newer entries retained
  assert.equal(deduplicator.isDuplicate({ id: 4 }), true)
})

// ── Permission State Tests ───────────────────────────────────────

test('getNotificationPermission & request: identifies Electron native permission', async () => {
  const fakeElectron = {
    showNotification: async () => ({ accepted: true, delivery_stage: 'api_accepted' }),
  }

  assert.equal(getNotificationPermission({ electronAPI: fakeElectron }), 'supported')
  const perm = await requestNotificationPermission({ electronAPI: fakeElectron })
  assert.equal(perm, 'supported')
})

test('getNotificationPermission & request: identifies browser permission states', async () => {
  const fakeBrowserDefault = {
    permission: 'default',
    requestPermission: async () => 'granted',
  }

  assert.equal(getNotificationPermission({ NotificationApi: fakeBrowserDefault }), 'default')
  const reqResult = await requestNotificationPermission({ NotificationApi: fakeBrowserDefault })
  assert.equal(reqResult, 'granted')

  const fakeBrowserDenied = {
    permission: 'denied',
    requestPermission: async () => 'denied',
  }
  assert.equal(getNotificationPermission({ NotificationApi: fakeBrowserDenied }), 'denied')
})

test('getNotificationPermission: handles missing Notification environment', async () => {
  assert.equal(getNotificationPermission({ electronAPI: null, NotificationApi: null }), 'unsupported')
  const req = await requestNotificationPermission({ electronAPI: null, NotificationApi: null })
  assert.equal(req, 'unsupported')
})

// ── Notification Dispatcher Tests ────────────────────────────────

test('createNotificationDispatcher: dispatches through Electron native channel when available', async () => {
  let electronCalledWith = null
  let toastCalledWith = null

  const fakeElectron = {
    showNotification: async (opts) => {
      electronCalledWith = opts
      return { accepted: true, channel: 'electron-native', delivery_stage: 'api_accepted', observed_by_user: false }
    },
    onNotificationClicked: () => () => {},
  }

  const dispatcher = createNotificationDispatcher({
    electronAPI: fakeElectron,
    onToast: (t) => { toastCalledWith = t },
  })

  const alert = { id: 101, kind: 'timer_due', message: 'Pomodoro finished' }
  const result = await dispatcher.dispatchAlert(alert)

  assert.equal(result.status, 'accepted')
  assert.equal(result.channel, 'electron-native')
  assert.equal(result.delivery_stage, 'api_accepted')
  assert.equal(result.observed_by_user, false)
  assert.equal(electronCalledWith.title, 'V.E.G.A. — Timer Expired')
  assert.equal(electronCalledWith.body, 'Pomodoro finished')
  assert.equal(toastCalledWith.title, 'V.E.G.A. — Timer Expired')

  // Redundant dispatch is suppressed by deduplicator
  const repeatResult = await dispatcher.dispatchAlert(alert)
  assert.equal(repeatResult.status, 'suppressed')
  assert.equal(repeatResult.reason, 'duplicate')

  dispatcher.cleanup()
})

test('createNotificationDispatcher: dispatches through Web Notification in browser', async () => {
  let webNotificationInstance = null
  let toastCalledWith = null

  class MockNotification {
    static permission = 'granted'
    constructor(title, options) {
      webNotificationInstance = { title, ...options }
    }
  }

  const dispatcher = createNotificationDispatcher({
    electronAPI: null,
    NotificationApi: MockNotification,
    onToast: (t) => { toastCalledWith = t },
  })

  const alert = { id: 102, kind: 'reminder_due', message: 'Drink water' }
  const result = await dispatcher.dispatchAlert(alert)

  assert.equal(result.status, 'accepted')
  assert.equal(result.channel, 'browser-native')
  assert.equal(result.delivery_stage, 'api_accepted')
  assert.equal(result.observed_by_user, false)
  assert.equal(webNotificationInstance.title, 'V.E.G.A. — Reminder')
  assert.equal(webNotificationInstance.body, 'Drink water')
  assert.equal(toastCalledWith.body, 'Drink water')

  dispatcher.cleanup()
})

test('createNotificationDispatcher: falls back gracefully to in-app banner when notifications denied', async () => {
  let toastCalledWith = null

  class DeniedNotification {
    static permission = 'denied'
  }

  const dispatcher = createNotificationDispatcher({
    electronAPI: null,
    NotificationApi: DeniedNotification,
    onToast: (t) => { toastCalledWith = t },
  })

  const alert = { id: 103, kind: 'task_deadline', message: 'Physics problem set due' }
  const result = await dispatcher.dispatchAlert(alert)

  assert.equal(result.status, 'accepted')
  assert.equal(result.channel, 'in-app-banner')
  assert.equal(result.delivery_stage, 'in_app_banner')
  assert.equal(result.observed_by_user, false)
  assert.equal(toastCalledWith.title, 'V.E.G.A. — Task Deadline')
  assert.equal(toastCalledWith.body, 'Physics problem set due')

  dispatcher.cleanup()
})

test('createNotificationDispatcher: fallback failure reports failed status and permits retry', async () => {
  let attempts = 0
  let shouldFail = true

  const dispatcher = createNotificationDispatcher({
    electronAPI: null,
    NotificationApi: null,
    onToast: () => {
      attempts++
      if (shouldFail) throw new Error('Toast container not ready')
    },
  })

  const alert = { id: 105, kind: 'timer_due', message: 'Take a break' }

  // First dispatch: onToast fails
  const res1 = await dispatcher.dispatchAlert(alert)
  assert.equal(res1.status, 'failed')
  assert.equal(res1.delivery_stage, 'failed')
  assert.equal(res1.observed_by_user, false)
  assert.equal(attempts, 1)

  // Deduplicator did NOT mark delivered, so retry is NOT suppressed as duplicate!
  shouldFail = false
  const res2 = await dispatcher.dispatchAlert(alert)
  assert.equal(res2.status, 'accepted')
  assert.equal(res2.delivery_stage, 'in_app_banner')
  assert.equal(res2.observed_by_user, false)
  assert.equal(attempts, 2)

  dispatcher.cleanup()
})

test('createNotificationDispatcher: hooks notification click navigation and cleans up', () => {
  let clickCallback = null
  let unlistenCalled = false
  let navigatedTo = null

  const fakeElectron = {
    showNotification: async () => ({ delivered: true }),
    onNotificationClicked: (cb) => {
      clickCallback = cb
      return () => { unlistenCalled = true }
    },
  }

  const dispatcher = createNotificationDispatcher({
    electronAPI: fakeElectron,
    onNavigate: (data) => { navigatedTo = data },
  })

  assert.ok(typeof clickCallback === 'function')
  clickCallback({ id: 104, kind: 'timer_due' })
  assert.deepEqual(navigatedTo, { id: 104, kind: 'timer_due' })

  dispatcher.cleanup()
  assert.equal(unlistenCalled, true)
})
