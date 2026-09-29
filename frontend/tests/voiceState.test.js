// M2a: voice UI state machine tests (node --test, no DOM).
// These lock in the two reproduced bugs: the "Transcribing..." banner that
// stuck forever when a capture ended in a terminal status, and the wake-word
// indicator that claimed "active" from mere WebSocket connectivity.

import test from 'node:test'
import assert from 'node:assert/strict'
import { initialVoiceUI, reduceVoiceEvent, voiceBannerLabel } from '../src/lib/voiceState.js'

const s = (...events) => events.reduce(reduceVoiceEvent, initialVoiceUI)

test('happy path: wake -> listening -> processing -> transcript -> idle', () => {
  const st = s(
    { type: 'mic', listening: true, note: '' },
    { type: 'wake' },
    { type: 'listening' },
    { type: 'processing' },
    { type: 'transcript', text: 'set a timer' },
  )
  assert.equal(st.voiceState, 'idle')
  assert.equal(st.micListening, true)
  assert.equal(st.statusNote, '')
})

test('regression: no-speech status is terminal (banner must not stick)', () => {
  const stuck = s({ type: 'processing' }, { type: 'status', text: 'No speech detected' })
  assert.equal(stuck.voiceState, 'idle')
  assert.equal(stuck.statusNote, 'No speech detected')
  // ...including the "too short" and "captured nothing" variants
  assert.equal(s({ type: 'processing' }, { type: 'status', text: 'Recording too short' }).voiceState, 'idle')
  assert.equal(s({ type: 'processing' }, { type: 'status', text: 'No speech captured' }).voiceState, 'idle')
})

test('transcription error is terminal and surfaces a note', () => {
  const st = s({ type: 'processing' }, { type: 'error', text: 'boom' })
  assert.equal(st.voiceState, 'idle')
  assert.equal(st.statusNote, 'boom')
})

test('mic events drive the indicator, not socket openness', () => {
  assert.equal(initialVoiceUI.micListening, false)
  assert.equal(s({ type: 'mic', listening: true, note: '' }).micListening, true)
  assert.equal(s({ type: 'mic', listening: false, note: 'Wake word off' }).micListening, false)
  assert.equal(s({ type: 'mic', listening: false, note: 'Wake word off' }).statusNote, 'Wake word off')
})

test('wake and listening clear the previous terminal note', () => {
  const st = s({ type: 'status', text: 'No speech detected' }, { type: 'wake' })
  assert.equal(st.statusNote, '')
  assert.equal(st.voiceState, 'wake')
})

test('unknown message types never corrupt the state', () => {
  const st = s({ type: 'mic', listening: true, note: '' }, { type: 'listening' })
  for (const junk of [{ type: 'nonsense' }, {}, null, 'x', 42]) {
    assert.deepEqual(reduceVoiceEvent(st, junk), st, JSON.stringify(junk))
  }
})

test('ws_close resets to initial state (indicator honest while reconnecting)', () => {
  const before = s({ type: 'mic', listening: true, note: '' }, { type: 'processing' })
  const after = reduceVoiceEvent(before, { type: 'ws_close' })
  assert.deepEqual(after, initialVoiceUI)
})

test('banner labels are honest per state', () => {
  assert.equal(voiceBannerLabel('processing'), 'Transcribing...')
  assert.equal(voiceBannerLabel('wake'), 'Wake detected!')
  assert.equal(voiceBannerLabel('idle'), null)
})
