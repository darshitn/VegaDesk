import { prepareSpeechText } from './speechText.js'

/**
 * Production Speech Session Manager
 * Manages utterance generational IDs, concurrency guards, cancellation,
 * and state transitions so cancelled utterances cannot reset newer utterances.
 */
export function createSpeechSession({
  speechSynthesis = typeof window !== 'undefined' ? window.speechSynthesis : null,
  onStateChange,
  onDuck,
} = {}) {
  let currentUtteranceId = 0
  let isSpeaking = false

  function speak(text, { onStart, onEnd, onError } = {}) {
    if (!speechSynthesis || typeof text !== 'string') return null
    const clean = prepareSpeechText(text)
    if (!clean) return null

    const utteranceId = ++currentUtteranceId

    try {
      speechSynthesis.cancel()
    } catch { /* noop */ }

    const utterance = new SpeechSynthesisUtterance(clean)

    utterance.onstart = () => {
      if (currentUtteranceId === utteranceId) {
        isSpeaking = true
        onStateChange?.(true)
        onDuck?.(true)
        onStart?.()
      }
    }

    utterance.onend = () => {
      if (currentUtteranceId === utteranceId) {
        isSpeaking = false
        onStateChange?.(false)
        onDuck?.(false)
        onEnd?.()
      }
    }

    utterance.onerror = (err) => {
      if (currentUtteranceId === utteranceId) {
        isSpeaking = false
        onStateChange?.(false)
        onDuck?.(false)
        onError?.(err)
      }
    }

    try {
      speechSynthesis.speak(utterance)
    } catch {
      if (currentUtteranceId === utteranceId) {
        isSpeaking = false
        onStateChange?.(false)
        onDuck?.(false)
      }
      return null
    }

    return { utteranceId, utterance }
  }

  function stopSpeaking() {
    currentUtteranceId++
    try {
      speechSynthesis?.cancel()
    } catch { /* noop */ }
    const wasSpeaking = isSpeaking
    isSpeaking = false
    if (wasSpeaking) {
      onStateChange?.(false)
      onDuck?.(false)
    }
    return { cancelledUtteranceId: currentUtteranceId - 1 }
  }

  function isCancelled(utteranceId) {
    return utteranceId !== currentUtteranceId
  }

  return {
    get isSpeaking() { return isSpeaking },
    get currentUtteranceId() { return currentUtteranceId },
    speak,
    stopSpeaking,
    isCancelled,
  }
}

/**
 * Send an acknowledgement to the backend that TTS was cancelled for a given interruption.
 * Tries the active WebSocket first; falls back to or supplements with HTTP POST.
 */
export function sendInterruptAck({
  ws,
  interruptionId,
  apiBase,
  fetchFn,
} = {}) {
  const reqId = String(interruptionId || '').trim()
  if (!reqId) return false

  let sentWs = false
  if (ws && typeof ws.send === 'function') {
    const isOpen = ws.readyState === (typeof WebSocket !== 'undefined' ? WebSocket.OPEN : 1)
    if (isOpen) {
      try {
        ws.send(JSON.stringify({ type: 'interrupt_ack', interruption_id: reqId }))
        sentWs = true
      } catch {
        sentWs = false
      }
    }
  }

  if (fetchFn && apiBase) {
    try {
      fetchFn(`${apiBase}/api/voice/interrupt_ack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interruption_id: reqId }),
        keepalive: true,
      }).catch(() => { /* silent */ })
    } catch { /* silent */ }
  }

  return sentWs || Boolean(fetchFn)
}

/**
 * Handle incoming voice messages from the /ws/voice WebSocket.
 * Guarantees that explicit interrupt events unconditionally cancel TTS
 * without depending on React's re-render or potentially delayed isSpeaking state.
 */
export function handleVoiceMessage(data, {
  speechSession,
  sendAck,
  onShowWindow,
  onTranscript,
  voiceDispatch,
} = {}) {
  if (!data || typeof data !== 'object') return { handled: false }

  if (data.type === 'wake') {
    let interrupted = false
    let acknowledged = false

    // Explicit interrupt: unconditionally cancel TTS playback immediately!
    if (data.interrupt) {
      speechSession?.stopSpeaking()
      interrupted = true
      if (data.interruption_id && sendAck) {
        sendAck(data.interruption_id)
        acknowledged = true
      }
    } else if (speechSession?.isSpeaking) {
      // Normal wake while speaking (fallback)
      speechSession.stopSpeaking()
      interrupted = true
    }

    if (onShowWindow) {
      try { onShowWindow() } catch { /* noop */ }
    }

    voiceDispatch?.(data)
    return { handled: true, interrupted, acknowledged }
  }

  if (data.type === 'transcript' && data.text && data.text.trim()) {
    onTranscript?.(data.text.trim())
    voiceDispatch?.(data)
    return { handled: true, transcript: data.text.trim() }
  }

  voiceDispatch?.(data)
  return { handled: true }
}
