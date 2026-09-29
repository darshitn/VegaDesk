import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  sanitizeMessage,
  boundMessages,
  loadMessages,
  saveMessages,
  clearMessages,
  CHAT_STORAGE_KEY,
  MAX_PERSISTED_MESSAGES,
} from '../src/lib/chatStore.js'

// Minimal in-memory Web Storage stand-in.
function fakeStorage(initial = {}) {
  const store = { ...initial }
  return {
    store,
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
  }
}

test('sanitizeMessage keeps only role + text content', () => {
  assert.deepEqual(sanitizeMessage({ role: 'user', content: 'hi' }), { role: 'user', content: 'hi' })
  assert.deepEqual(sanitizeMessage({ role: 'assistant', content: 'yo' }), { role: 'assistant', content: 'yo' })
})

test('sanitizeMessage rejects non-text content (audio blobs, objects)', () => {
  assert.equal(sanitizeMessage({ role: 'user', content: new Uint8Array([1, 2, 3]) }), null)
  assert.equal(sanitizeMessage({ role: 'user', content: { blob: 'audio' } }), null)
  assert.equal(sanitizeMessage({ role: 'user', content: null }), null)
  assert.equal(sanitizeMessage({ role: 'user', content: 42 }), null)
})

test('sanitizeMessage rejects bad roles and strips extra fields', () => {
  assert.equal(sanitizeMessage({ role: 'system', content: 'x' }), null)
  assert.equal(sanitizeMessage({ role: 'tool', content: 'x' }), null)
  assert.equal(sanitizeMessage({ content: 'x' }), null)
  // Extra fields (e.g. a secret token) must never be persisted.
  const s = sanitizeMessage({ role: 'user', content: 'ok', apiKey: 'sk-secret', audio: 'AAAA' })
  assert.deepEqual(s, { role: 'user', content: 'ok' })
})

test('sanitizeMessage drops empty/whitespace and truncates oversized text', () => {
  assert.equal(sanitizeMessage({ role: 'user', content: '   ' }), null)
  const long = sanitizeMessage({ role: 'user', content: 'a'.repeat(9000) })
  assert.equal(long.content.length, 4000)
})

test('sanitizeMessage keeps a valid execution mode and drops anything else', () => {
  // P1: the small execution-mode badge survives a reload, but only for the
  // four values the backend may report; unknown/extra values are stripped.
  assert.deepEqual(
    sanitizeMessage({ role: 'assistant', content: 'done', mode: 'deterministic' }),
    { role: 'assistant', content: 'done', mode: 'deterministic' })
  assert.deepEqual(
    sanitizeMessage({ role: 'assistant', content: 'ok', mode: 'cloud' }),
    { role: 'assistant', content: 'ok', mode: 'cloud' })
  assert.deepEqual(
    sanitizeMessage({ role: 'assistant', content: 'x', mode: 'evil', apiKey: 'sk-1' }),
    { role: 'assistant', content: 'x' })
  assert.deepEqual(
    sanitizeMessage({ role: 'assistant', content: 'y', mode: 42 }),
    { role: 'assistant', content: 'y' })
})

test('boundMessages keeps only the most recent N valid messages', () => {
  const many = Array.from({ length: MAX_PERSISTED_MESSAGES + 20 }, (_, i) => ({ role: 'user', content: `m${i}` }))
  const bounded = boundMessages(many)
  assert.equal(bounded.length, MAX_PERSISTED_MESSAGES)
  assert.equal(bounded[bounded.length - 1].content, `m${MAX_PERSISTED_MESSAGES + 19}`)
})

test('boundMessages filters invalid entries and handles non-arrays', () => {
  const mixed = [
    { role: 'user', content: 'keep' },
    { role: 'nope', content: 'drop' },
    { role: 'assistant', content: new Uint8Array([9]) },
    { role: 'assistant', content: 'also keep' },
  ]
  assert.deepEqual(boundMessages(mixed), [
    { role: 'user', content: 'keep' },
    { role: 'assistant', content: 'also keep' },
  ])
  assert.deepEqual(boundMessages(null), [])
  assert.deepEqual(boundMessages('not an array'), [])
})

test('saveMessages then loadMessages round-trips sanitized, bounded history', () => {
  const storage = fakeStorage()
  saveMessages([
    { role: 'user', content: 'hello' },
    { role: 'assistant', content: 'hi there', secret: 'nope' },
    { role: 'user', content: new Uint8Array([1]) }, // dropped
  ], storage)
  const loaded = loadMessages(storage)
  assert.deepEqual(loaded, [
    { role: 'user', content: 'hello' },
    { role: 'assistant', content: 'hi there' },
  ])
  // The serialized form must not contain the dropped non-text field.
  assert.equal(storage.store[CHAT_STORAGE_KEY].includes('secret'), false)
})

test('loadMessages returns [] on corrupt or missing storage', () => {
  assert.deepEqual(loadMessages(fakeStorage({ [CHAT_STORAGE_KEY]: '{not json' })), [])
  assert.deepEqual(loadMessages(fakeStorage({ [CHAT_STORAGE_KEY]: '{"a":1}' })), []) // object, not array
  assert.deepEqual(loadMessages(fakeStorage()), [])
  assert.deepEqual(loadMessages(null), [])
})

test('clearMessages removes persisted history', () => {
  const storage = fakeStorage()
  saveMessages([{ role: 'user', content: 'remember me' }], storage)
  assert.equal(loadMessages(storage).length, 1)
  clearMessages(storage)
  assert.equal(CHAT_STORAGE_KEY in storage.store, false)
  assert.deepEqual(loadMessages(storage), [])
})

test('saveMessages swallows storage errors (quota / privacy mode)', () => {
  const throwing = {
    getItem: () => { throw new Error('blocked') },
    setItem: () => { throw new Error('quota') },
    removeItem: () => { throw new Error('blocked') },
  }
  assert.doesNotThrow(() => saveMessages([{ role: 'user', content: 'x' }], throwing))
  assert.doesNotThrow(() => clearMessages(throwing))
  assert.deepEqual(loadMessages(throwing), [])
})
