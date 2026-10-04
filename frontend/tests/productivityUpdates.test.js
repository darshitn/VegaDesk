import test from 'node:test'
import assert from 'node:assert/strict'
import { publishProductivityUpdate, subscribeProductivityUpdates } from '../src/lib/productivityUpdates.js'

test('committed chat and voice tasks refresh a mounted list, including replay', async () => {
  const stored = []
  let displayed = []
  let reads = 0
  let pending
  const stop = subscribeProductivityUpdates(() => {
    pending = Promise.resolve({ json: async () => stored.slice() })
      .then(response => response.json()).then(tasks => { displayed = tasks; reads++ })
  })
  try {
    for (const source of ['chat', 'voice']) {
      stored.push({ id: stored.length + 1, text: `${source} task`, completed: false })
      assert.equal(publishProductivityUpdate({ receipt: { action: 'create_task', success: true, source } }), true)
      await pending
      assert.deepEqual(displayed, stored)
    }
    publishProductivityUpdate({ receipt: { action: 'create_task', success: true, replayed: true } })
    await pending
    assert.equal(reads, 3)
    assert.equal(displayed.length, 2)
  } finally { stop() }
})

test('failed, ambiguous, read-only, unrelated and prose-only replies do not refresh', () => {
  let reads = 0
  const stop = subscribeProductivityUpdates(() => { reads++ })
  try {
    for (const response of [
      { response: 'Task added!' },
      { error: 'Denied', receipt: { action: 'create_task', success: true } },
      { receipt: { action: 'create_task', success: false } },
      { receipt: { action: 'create_task', success: 'true' } },
      { receipt: { action: 'create_task', success: true, clarification: true } },
      { receipt: { action: 'create_task', success: true, read_only: true } },
      { receipt: { action: 'list_tasks', success: true, read_only: true } },
      { receipt: { action: 'open_app', success: true } },
      null,
    ]) assert.equal(publishProductivityUpdate(response), false)
    assert.equal(reads, 0)
  } finally { stop() }
})

test('unmounted views unsubscribe and a broken view cannot block other views', () => {
  let reads = 0
  const stopBroken = subscribeProductivityUpdates(() => { throw new Error('view failure') })
  const stop = subscribeProductivityUpdates(() => { reads++ })
  try {
    publishProductivityUpdate({ receipt: { action: 'set_task_completed', success: true } })
    assert.equal(reads, 1)
    stop()
    publishProductivityUpdate({ receipt: { action: 'create_task', success: true } })
    assert.equal(reads, 1)
  } finally { stop(); stopBroken() }
})
