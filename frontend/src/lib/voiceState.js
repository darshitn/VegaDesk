// Pure state machine for the wake-word UI, driven by /ws/voice messages.
//
// Extracted so the transitions that previously broke are unit-testable with
// `node --test`:
//  - a "processing" state used to stick forever when the backend finished with
//    a terminal "status" (no-speech) instead of a transcript — status/error are
//    now terminal and return to idle with a human note;
//  - the wake-word indicator must reflect the backend MIC state ("mic"
//    events), not merely that the WebSocket connected.

export const initialVoiceUI = {
  voiceState: 'idle', // idle | wake | listening | processing
  micListening: false,
  statusNote: '',
}

export function reduceVoiceEvent(state, msg) {
  const type = msg && typeof msg.type === 'string' ? msg.type : ''
  switch (type) {
    case 'mic':
      return {
        ...state,
        micListening: !!msg.listening,
        statusNote: typeof msg.note === 'string' && msg.note ? msg.note : state.statusNote,
      }
    case 'wake':
      return { ...state, voiceState: 'wake', statusNote: '' }
    case 'listening':
      return { ...state, voiceState: 'listening', statusNote: '' }
    case 'processing':
      return { ...state, voiceState: 'processing' }
    case 'transcript':
      return { ...state, voiceState: 'idle', statusNote: '' }
    case 'status':
      // Terminal notice from a run that captured no usable speech.
      return { ...state, voiceState: 'idle', statusNote: String(msg.text || '').trim() }
    case 'error':
      return { ...state, voiceState: 'idle', statusNote: String(msg.text || 'Voice error').trim() }
    case 'ws_close':
      return { ...initialVoiceUI }
    default:
      return state
  }
}

export function voiceBannerLabel(voiceState) {
  return {
    wake: 'Wake detected!',
    listening: 'Listening...',
    processing: 'Transcribing...',
  }[voiceState] || null
}
