import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Cpu, Mic, MicOff, Volume2, VolumeX, Loader, Radio } from 'lucide-react'

export default function AIBrain({ userName, provider, voiceState, wakeWordActive, registerSubmitTranscript }) {
  const [messages, setMessages] = useState([])
  const messagesRef = useRef([]) // latest messages for transcript auto-submit (registered in App)

  // Keep ref in sync
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  const [input, setInput] = useState('')
  const [isThinking, setIsThinking] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [isMuted, setIsMuted] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)

  const messagesEndRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef = useRef([])
  const streamRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, isThinking])

  // ──────────────────────────────────────────
  // Voice transcript auto-submit (events come from App's WebSocket)
  // ──────────────────────────────────────────

  const stopMicStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
  }, [])

  const speakText = useCallback((text) => {
    if (isMuted || !window.speechSynthesis) return

    // Never read raw JSON / tool dumps / markdown symbols aloud
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

  const handleSendMessage = useCallback(async (text, history) => {
    if (!text.trim()) return

    const userMessage = { role: 'user', content: text.trim() }

    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsThinking(true)

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 90000)
    try {
      const response = await fetch('http://localhost:8000/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage.content,
          history: Array.isArray(history) ? history.slice(-20) : [],
          userName: userName,
          provider: provider
        }),
        signal: controller.signal
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()

      if (data.error) {
        const errorMsg = `[SYSTEM ERROR] ${data.error}`
        setMessages(prev => [...prev, { role: 'assistant', content: errorMsg }])
        speakText("System error encountered.")
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.response || '(empty response)' }])
        if (data.response) speakText(data.response)
        // Launched an app/website: let the confirmation show briefly, then get
        // out of the way so it appears on top of the always-on-top dashboard
        // (Ctrl+Space brings the dashboard back).
        if (data.opened && window.electronAPI) {
          setTimeout(() => { try { window.electronAPI.hideWindow() } catch {} }, 1500)
        }
      }
    } catch (err) {
      const msg = err.name === 'AbortError' ? 'Request timed out. Please try again.' : 'Connection failed. Is the backend running on port 8000?'
      setMessages(prev => [...prev, { role: 'assistant', content: `[SYSTEM ERROR] ${msg}` }])
    } finally {
      clearTimeout(timeoutId)
      setIsThinking(false)
    }
  }, [userName, provider, speakText])

  // ──────────────────────────────────────────
  // Voice transcript auto-submit (events come from App's WebSocket, which
  // stays connected while the dashboard is hidden — see App.jsx)
  // ──────────────────────────────────────────

  const submitTranscript = useCallback((text) => {
    handleSendMessage(text, messagesRef.current)
  }, [handleSendMessage])

  // Register with App so wake-word transcripts reach us even if we mount
  // after the transcript arrived (dashboard was hidden at wake time)
  useEffect(() => {
    registerSubmitTranscript(submitTranscript)
    return () => registerSubmitTranscript(null)
  }, [submitTranscript, registerSubmitTranscript])

  // Cleanup mic on unmount
  useEffect(() => {
    return () => {
      stopMicStream()
    }
  }, [stopMicStream])

  // ──────────────────────────────────────────
  // Manual mic button (fallback)
  // ──────────────────────────────────────────

  const toggleListening = async () => {
    if (isListening) {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop()
      }
      setIsListening(false)
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : 'audio/ogg'

      const recorder = new MediaRecorder(stream, { mimeType })
      mediaRecorderRef.current = recorder
      audioChunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        stopMicStream()
        const blob = new Blob(audioChunksRef.current, { type: mimeType })
        audioChunksRef.current = []
        await sendForTranscription(blob)
      }

      recorder.start()
      setIsListening(true)
    } catch (err) {
      console.error('Microphone error:', err)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `[MIC ERROR] ${err.message}. Make sure your microphone is connected and the browser has permission to use it.`
      }])
      setIsListening(false)
    }
  }

  const sendForTranscription = async (blob) => {
    if (!blob || blob.size < 1000) {
      setMessages(prev => [...prev, { role: 'assistant', content: '[TRANSCRIPTION] Recording too short.' }])
      return
    }
    setIsTranscribing(true)
    try {
      const formData = new FormData()
      formData.append('file', blob, 'recording.webm')

      const controller = new AbortController()
      const t = setTimeout(() => controller.abort(), 30000)
      const res = await fetch('http://localhost:8000/api/transcribe', {
        method: 'POST',
        body: formData,
        signal: controller.signal
      })
      clearTimeout(t)

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || `HTTP ${res.status}`)
      }

      const data = await res.json()
      if (data.transcript && data.transcript.trim()) {
        setInput(prev => prev + (prev ? ' ' : '') + data.transcript.trim())
      } else if (data.detail) {
        setMessages(prev => [...prev, { role: 'assistant', content: `[TRANSCRIPTION ERROR] ${data.detail}` }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: '[TRANSCRIPTION] No speech detected.' }])
      }
    } catch (err) {
      const msg = err.name === 'AbortError' ? 'Transcription timed out.' : err.message
      setMessages(prev => [...prev, { role: 'assistant', content: `[TRANSCRIPTION ERROR] Could not reach backend: ${msg}` }])
    } finally {
      setIsTranscribing(false)
    }
  }

  // ──────────────────────────────────────────
  // Manual send via input box
  // ──────────────────────────────────────────
  const handleSend = async () => {
    const currentHistory = [...messages]
    handleSendMessage(input, currentHistory)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const micBusy = isListening || isTranscribing
  const isVoiceActive = voiceState !== 'idle'

  // Status label for voice state
  const voiceStatusLabel = {
    idle: null,
    wake: 'Wake detected!',
    listening: 'Listening...',
    processing: 'Transcribing...',
  }[voiceState]

  return (
    <div className="flex flex-col h-full w-full max-w-3xl mx-auto rounded-lg border border-current/20 bg-black/10 backdrop-blur-sm overflow-hidden shadow-inner">
      
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-current/20 bg-black/20">
        <div className="flex items-center gap-2">
          <Cpu size={18} className="text-[var(--accent)]" />
          <h2 className="text-sm font-semibold tracking-wider">A.I. COGNITIVE CORE</h2>
          
          {/* Wake word status indicator */}
          <span className={`flex items-center gap-1 ml-2 text-xs ${wakeWordActive ? 'text-green-400' : 'text-red-400'}`}>
            <Radio size={12} className={isVoiceActive ? 'animate-pulse' : ''} />
            {wakeWordActive ? (voiceStatusLabel || 'Wake word active') : 'Voice offline'}
          </span>

          {isSpeaking && (
            <span className="flex items-center gap-1 ml-2">
              <span className="w-1 h-3 bg-[var(--accent)] animate-pulse" style={{animationDelay: '0ms'}} />
              <span className="w-1 h-4 bg-[var(--accent)] animate-pulse" style={{animationDelay: '150ms'}} />
              <span className="w-1 h-3 bg-[var(--accent)] animate-pulse" style={{animationDelay: '300ms'}} />
            </span>
          )}
        </div>
        <button 
          onClick={() => {
             setIsMuted(!isMuted)
             if (!isMuted) window.speechSynthesis?.cancel()
          }}
          className="p-1 opacity-70 hover:opacity-100 transition-opacity text-[var(--accent)]"
          title={isMuted ? "Unmute Voice" : "Mute Voice"}
        >
          {isMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}
        </button>
      </div>

      {/* Voice activity banner */}
      {isVoiceActive && (
        <div className="px-3 py-1.5 bg-[var(--accent)]/10 border-b border-[var(--accent)]/20 flex items-center gap-2 text-xs">
          <span className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
          <span className="text-[var(--accent)] font-semibold tracking-wide">{voiceStatusLabel}</span>
        </div>
      )}

      {/* Message History */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center opacity-30 text-sm italic text-center">
            <p>System initialized.</p>
            <p>Say "Hey Jarvis" or type below.</p>
          </div>
        )}
        
        {messages.map((msg, idx) => (
          <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap leading-relaxed shadow-sm
              ${msg.role === 'user' 
                ? 'font-medium' 
                : 'bg-black/40 border border-current/10'
              }`}
              style={msg.role === 'user' ? { backgroundColor: 'var(--accent)', color: '#000000' } : {}}
            >
              {msg.content}
            </div>
          </div>
        ))}
        
        {isThinking && (
          <div className="flex justify-start">
            <div className="bg-black/40 border border-current/10 rounded-lg px-4 py-3 flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" style={{ animationDelay: '0ms' }} />
              <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" style={{ animationDelay: '150ms' }} />
              <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="p-3 bg-black/20 border-t border-current/20 flex gap-2 items-end">
        <button 
          onClick={toggleListening}
          disabled={isTranscribing}
          className={`relative p-2 rounded-full transition-all shrink-0
            ${isListening 
              ? 'bg-[var(--accent)]/30 text-[var(--accent)]' 
              : isTranscribing 
                ? 'text-[var(--accent)] opacity-70'
                : 'text-current opacity-50 hover:opacity-100'
            }`}
          title={isListening ? "Stop recording" : isTranscribing ? "Transcribing..." : "Manual voice input (fallback)"}
        >
          {isListening && (
            <span className="absolute inset-0 rounded-full bg-[var(--accent)] opacity-20 animate-ping" />
          )}
          {isTranscribing 
            ? <Loader size={18} className="animate-spin" />
            : isListening 
              ? <Mic size={18} /> 
              : <MicOff size={18} />
          }
        </button>
        <textarea
          className="flex-1 bg-transparent resize-none outline-none text-sm p-2 max-h-32 min-h-[40px] font-inherit"
          placeholder={micBusy ? (isListening ? "Listening... click mic again to send" : "Transcribing...") : 'Say "Hey Jarvis" or type here...'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
        />
        <button 
          onClick={handleSend}
          disabled={!input.trim() || isThinking}
          className="p-2 rounded-full text-[var(--accent)] hover:bg-[var(--accent)]/20 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  )
}
