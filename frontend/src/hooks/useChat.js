import { useState, useRef, useEffect, useCallback } from 'react'
import { loadMessages, saveMessages, clearMessages } from '../lib/chatStore'
import { getApiBase, getApiPort, verifiedFetch } from '../lib/apiConfig'

// Tell the backend wake-word listener that VEGA is speaking (or has stopped),
// so the mic ignores VEGA's own voice. Fire-and-forget; the server also
// safety-expires a stuck "ducked" flag on its own.
function postVoiceDuck(active) {
  try {
    verifiedFetch(`${getApiBase()}/api/voice/duck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
      keepalive: true,
    }).catch(() => { /* backend down or session unverified */ })
  } catch { /* no fetch */ }
}

// Conversation state lifted out of AIBrain into App (which never unmounts), so
// theme switches, overlay hide/show and settings changes preserve messages and
// any in-flight response. History is persisted (text only, bounded) across
// restarts; duplicate sends are blocked; TTS lives here so a reply that arrives
// while the overlay is hidden is still spoken.
export default function useChat({ userName, provider }) {
  const [messages, setMessages] = useState(() => loadMessages())
  const [isThinking, setIsThinking] = useState(false)
  const [isMuted, setIsMuted] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)

  const messagesRef = useRef(messages)
  useEffect(() => { messagesRef.current = messages }, [messages])

  // Persist bounded, sanitized history on every change.
  useEffect(() => { saveMessages(messages) }, [messages])

  // Duck the wake-word mic exactly while our own TTS is audible, so VEGA can
  // never hear itself as a command. Edge (true→false) also covers cancel().
  const duckRef = useRef(false)
  useEffect(() => {
    if (isSpeaking !== duckRef.current) {
      duckRef.current = isSpeaking
      postVoiceDuck(isSpeaking)
    }
  }, [isSpeaking])

  const inFlightRef = useRef(false)
  const configRef = useRef({ userName, provider })
  useEffect(() => { configRef.current = { userName, provider } }, [userName, provider])

  const speakText = useCallback((text) => {
    if (isMuted || typeof window === 'undefined' || !window.speechSynthesis) return
    const clean = String(text)
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/\{[\s\S]*?\}/g, ' ')
      .replace(/[*_#`>|]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (!clean) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(clean)
    utterance.onstart = () => setIsSpeaking(true)
    utterance.onend = () => setIsSpeaking(false)
    utterance.onerror = () => setIsSpeaking(false)
    window.speechSynthesis.speak(utterance)
  }, [isMuted])

  const sendMessage = useCallback(async (text, opts = {}) => {
    const value = typeof text === 'string' ? text.trim() : ''
    if (!value) return
    if (inFlightRef.current) return // prevent duplicate concurrent sends

    inFlightRef.current = true
    const history = messagesRef.current.slice(-20)
    const { userName: un, provider: prov } = configRef.current
    const source = opts.source === 'voice' ? 'voice' : 'chat'

    setMessages(prev => [...prev, { role: 'user', content: value }])
    setIsThinking(true)

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 90000)
    try {
      const response = await verifiedFetch(`${getApiBase()}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: value, history, userName: un, provider: prov, source }),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      if (data.error) {
        setMessages(prev => [...prev, { role: 'assistant', content: `[SYSTEM ERROR] ${data.error}` }])
        speakText('System error encountered.')
      } else {
        const mode = ['deterministic', 'local', 'cloud', 'none'].includes(data.executionMode)
          ? data.executionMode : undefined
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.response || '(empty response)',
          ...(mode ? { mode } : {}),
        }])
        if (data.response) speakText(data.response)
        // Launched an app/website: let the confirmation show briefly, then hide
        // the dashboard so the launched window appears on top of it.
        if (data.opened && typeof window !== 'undefined' && window.electronAPI) {
          setTimeout(() => { try { window.electronAPI.hideWindow() } catch { /* noop */ } }, 1500)
        }
      }
    } catch (err) {
      const msg = err && err.name === 'AbortError'
        ? 'Request timed out. Please try again.'
        : err?.message?.includes('Mutation blocked')
          ? err.message
          : `Connection failed. Is the backend running on port ${getApiPort()}?`
      setMessages(prev => [...prev, { role: 'assistant', content: `[SYSTEM ERROR] ${msg}` }])
    } finally {
      clearTimeout(timeoutId)
      setIsThinking(false)
      inFlightRef.current = false
    }
  }, [speakText])

  // For local system/mic notices that aren't a send round-trip.
  const appendMessage = useCallback((msg) => {
    setMessages(prev => [...prev, msg])
  }, [])

  const clearHistory = useCallback(() => {
    setMessages([])
    messagesRef.current = []
    clearMessages()
  }, [])

  return {
    messages, isThinking, isMuted, isSpeaking,
    setIsMuted, speakText, sendMessage, appendMessage, clearHistory,
  }
}
