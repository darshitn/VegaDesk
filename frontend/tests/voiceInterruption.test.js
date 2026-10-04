import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  createSpeechSession,
  sendInterruptAck,
  handleVoiceMessage,
} from '../src/lib/voiceInterruption.js'

// Polyfill SpeechSynthesisUtterance for node test environment
if (typeof globalThis.SpeechSynthesisUtterance === 'undefined') {
  globalThis.SpeechSynthesisUtterance = class {
    constructor(text) {
      this.text = text
      this.onstart = null
      this.onend = null
      this.onerror = null
    }
  }
}

function createMockSpeechSynthesis() {
  let cancelCalls = 0
  const spoken = []
  return {
    get cancelCalls() { return cancelCalls },
    get spoken() { return spoken },
    cancel() {
      cancelCalls++
    },
    speak(utt) {
      spoken.push(utt)
    },
  }
}

test('production speech session: cancelled utterance callbacks cannot reset newer utterance', () => {
  const mockSynth = createMockSpeechSynthesis()
  let stateChanges = []
  const session = createSpeechSession({
    speechSynthesis: mockSynth,
    onStateChange: (speaking) => stateChanges.push(speaking),
  })

  // Start utterance 1
  const res1 = session.speak('First explanation about binary trees.')
  assert.ok(res1)
  assert.equal(res1.utteranceId, 1)
  // Utterance 1 starts speaking
  res1.utterance.onstart()
  assert.equal(session.isSpeaking, true)

  // Utterance 2 starts, superseding utterance 1
  const res2 = session.speak('Second explanation about sorting.')
  assert.ok(res2)
  assert.equal(res2.utteranceId, 2)
  res2.utterance.onstart()
  assert.equal(session.isSpeaking, true)
  assert.equal(session.isCancelled(res1.utteranceId), true)
  assert.equal(session.isCancelled(res2.utteranceId), false)

  // Utterance 1's delayed cancellation callback arrives now
  res1.utterance.onerror(new Error('interrupted'))
  // Stale callback MUST NOT reset isSpeaking
  assert.equal(session.isSpeaking, true, 'Speaking state must remain true for newer utterance')

  res1.utterance.onend()
  assert.equal(session.isSpeaking, true, 'onend from cancelled utterance must be ignored')

  // When Utterance 2 ends normally, state resets cleanly
  res2.utterance.onend()
  assert.equal(session.isSpeaking, false)
})

test('production speech session: stopSpeaking invalidates current utterance and cancels synthesis', () => {
  const mockSynth = createMockSpeechSynthesis()
  const session = createSpeechSession({ speechSynthesis: mockSynth })

  const res = session.speak('Long audio reply')
  res.utterance.onstart()
  assert.equal(session.isSpeaking, true)

  const stopResult = session.stopSpeaking()
  assert.equal(session.isSpeaking, false)
  assert.equal(mockSynth.cancelCalls >= 1, true)
  assert.equal(session.isCancelled(res.utteranceId), true)

  // Firing delayed callback has no effect
  res.utterance.onerror(new Error('cancelled'))
  assert.equal(session.isSpeaking, false)
})

test('handleVoiceMessage: unconditionally cancels speech on interrupt event without relying on isSpeaking', () => {
  const mockSynth = createMockSpeechSynthesis()
  const session = createSpeechSession({ speechSynthesis: mockSynth })
  // Simulate React state delay: speech session isSpeaking is currently false
  assert.equal(session.isSpeaking, false)

  let ackSent = null
  const res = handleVoiceMessage(
    { type: 'wake', interrupt: true, interruption_id: 'int-abc-123' },
    {
      speechSession: session,
      sendAck: (id) => { ackSent = id },
      voiceDispatch: () => {},
    }
  )

  assert.equal(res.handled, true)
  assert.equal(res.interrupted, true)
  assert.equal(res.acknowledged, true)
  assert.equal(ackSent, 'int-abc-123')
  assert.equal(mockSynth.cancelCalls >= 1, true)
})

test('sendInterruptAck: sends via WebSocket when open and falls back to HTTP POST', () => {
  const wsMessages = []
  const mockWs = {
    readyState: 1, // OPEN
    send(msg) { wsMessages.push(JSON.parse(msg)) },
  }

  const postCalls = []
  const mockFetch = async (url, opts) => {
    postCalls.push({ url, body: JSON.parse(opts.body) })
    return { ok: true }
  }

  // 1. WebSocket open
  const okWs = sendInterruptAck({
    ws: mockWs,
    interruptionId: 'ack-789',
    apiBase: 'http://localhost:8000',
    fetchFn: mockFetch,
  })
  assert.equal(okWs, true)
  assert.equal(wsMessages.length, 1)
  assert.deepEqual(wsMessages[0], { type: 'interrupt_ack', interruption_id: 'ack-789' })
  assert.equal(postCalls.length, 1)
  assert.deepEqual(postCalls[0].body, { interruption_id: 'ack-789' })

  // 2. Missing interruptionId fails safely
  assert.equal(sendInterruptAck({ ws: mockWs, interruptionId: null }), false)
  assert.equal(sendInterruptAck({ ws: mockWs, interruptionId: '' }), false)

  // 3. Closed WebSocket falls back to fetchFn without crashing
  const closedWs = { readyState: 3 } // CLOSED
  const okClosed = sendInterruptAck({
    ws: closedWs,
    interruptionId: 'ack-closed-1',
    apiBase: 'http://localhost:8000',
    fetchFn: mockFetch,
  })
  assert.equal(okClosed, true)
  assert.equal(postCalls.length, 2)
  assert.equal(postCalls[1].body.interruption_id, 'ack-closed-1')
})

test('handleVoiceMessage: handles repeated interruptions cleanly', () => {
  const mockSynth = createMockSpeechSynthesis()
  const session = createSpeechSession({ speechSynthesis: mockSynth })
  session.speak('Playing speech.')

  const acks = []
  const sendAck = (id) => acks.push(id)

  // First interrupt
  handleVoiceMessage(
    { type: 'wake', interrupt: true, interruption_id: 'int-1' },
    { speechSession: session, sendAck }
  )
  assert.equal(acks[0], 'int-1')
  const uttIdAfterFirst = session.currentUtteranceId

  // Second interrupt in rapid succession
  handleVoiceMessage(
    { type: 'wake', interrupt: true, interruption_id: 'int-2' },
    { speechSession: session, sendAck }
  )
  assert.equal(acks[1], 'int-2')
  assert.ok(session.currentUtteranceId > uttIdAfterFirst)
  assert.equal(session.isSpeaking, false)
})

test('handleVoiceMessage: normal wake without interrupt flag does not send ack', () => {
  const mockSynth = createMockSpeechSynthesis()
  const session = createSpeechSession({ speechSynthesis: mockSynth })

  let ackCalled = false
  const res = handleVoiceMessage(
    { type: 'wake' },
    {
      speechSession: session,
      sendAck: () => { ackCalled = true },
      voiceDispatch: () => {},
    }
  )

  assert.equal(res.handled, true)
  assert.equal(res.interrupted, false)
  assert.equal(res.acknowledged, false)
  assert.equal(ackCalled, false)
})
