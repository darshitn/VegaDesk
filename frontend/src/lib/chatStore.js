// Chat-history persistence for the A.I. core.
//
// Pure module (no React, no browser globals at import time) so it can be unit
// tested with `node --test`. Only plain {role, content} text is ever stored:
// audio blobs, tool payloads, objects and oversized strings are dropped, so raw
// microphone audio and any secret-like non-text value can never reach
// localStorage. History is bounded to the most recent MAX_PERSISTED_MESSAGES.

export const CHAT_STORAGE_KEY = 'jarvisChatHistory'
export const MAX_PERSISTED_MESSAGES = 60
export const MAX_MESSAGE_CHARS = 4000

const VALID_ROLES = new Set(['user', 'assistant'])
// Execution-mode chip values the backend may report (P1). Persisted so the
// badge survives a reload; anything else is dropped.
const VALID_MODES = new Set(['deterministic', 'local', 'cloud', 'none'])

function defaultStorage() {
  try {
    if (typeof globalThis !== 'undefined' && globalThis.localStorage) return globalThis.localStorage
  } catch { /* no storage in this environment */ }
  return null
}

// Returns a clean {role, content} (plus optional mode) or null when the value
// must not be persisted.
export function sanitizeMessage(msg) {
  if (!msg || typeof msg !== 'object') return null
  if (typeof msg.role !== 'string' || !VALID_ROLES.has(msg.role)) return null
  if (typeof msg.content !== 'string') return null // reject blobs/objects/arrays
  const content = msg.content.slice(0, MAX_MESSAGE_CHARS)
  if (!content.trim()) return null
  const out = { role: msg.role, content }
  if (typeof msg.mode === 'string' && VALID_MODES.has(msg.mode)) out.mode = msg.mode
  return out
}

// Sanitize + keep only the most recent `max` messages.
export function boundMessages(list, max = MAX_PERSISTED_MESSAGES) {
  if (!Array.isArray(list)) return []
  const clean = []
  for (const m of list) {
    const s = sanitizeMessage(m)
    if (s) clean.push(s)
  }
  return clean.slice(Math.max(0, clean.length - max))
}

export function loadMessages(storage = defaultStorage()) {
  if (!storage) return []
  try {
    const raw = storage.getItem(CHAT_STORAGE_KEY)
    if (!raw) return []
    return boundMessages(JSON.parse(raw))
  } catch {
    return [] // corrupt or unreadable storage -> start fresh, never throw
  }
}

export function saveMessages(messages, storage = defaultStorage()) {
  if (!storage) return
  try {
    storage.setItem(CHAT_STORAGE_KEY, JSON.stringify(boundMessages(messages)))
  } catch { /* quota / privacy mode — history simply isn't persisted */ }
}

export function clearMessages(storage = defaultStorage()) {
  if (!storage) return
  try {
    storage.removeItem(CHAT_STORAGE_KEY)
  } catch { /* ignore */ }
}
