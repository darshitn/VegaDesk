import { test } from 'node:test'
import assert from 'node:assert/strict'
import { confirmVoicePreference } from '../src/lib/voiceSettings.js'
import { DEFAULT_SETUP_CONFIG } from '../src/lib/setupConfig.js'

test('new profiles and onboarding default to a visible pinned window', () => {
  assert.equal(DEFAULT_SETUP_CONFIG.mode, 'Pinned Desktop')
})

test('voice preference follows the server confirmation rather than the requested value', async () => {
  let requested
  const confirmed = await confirmVoicePreference(async enabled => {
    requested = enabled
    return { ok: true, json: async () => ({ enabled: false }) }
  }, true)
  assert.equal(requested, true)
  assert.equal(confirmed, false)
})

test('voice refusal preserves the actionable server error', async () => {
  await assert.rejects(confirmVoicePreference(async () => ({
    ok: true, json: async () => ({ enabled: false, error: 'Voice is disabled in this profile.' }),
  }), true), /Voice is disabled in this profile/)
})

test('voice HTTP, malformed, and connection failures never return a confirmed preference', async () => {
  await assert.rejects(confirmVoicePreference(async () => ({ ok: false, status: 503 }), true), /HTTP 503/)
  await assert.rejects(confirmVoicePreference(async () => ({ ok: true, json: async () => ({}) }), false), /did not confirm/)
  await assert.rejects(confirmVoicePreference(async () => { throw new Error('Offline') }, true), /Offline/)
})
