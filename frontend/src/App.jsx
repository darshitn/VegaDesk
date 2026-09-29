import { useState, useEffect, useRef, useCallback, useReducer } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Settings, X, Camera, Maximize2, Minimize2, Bell } from 'lucide-react'
import AIBrain from './components/AIBrain'
import SystemMonitor from './components/SystemMonitor'
import LiveFeeds from './components/LiveFeeds'
import ProductivityHub from './components/ProductivityHub'
import SetupWizard from './components/SetupWizard'
import NeuralCosmos from './components/NeuralCosmos'
import HandGestureController from './components/HandGestureController'
import useChat from './hooks/useChat'
import { initialVoiceUI, reduceVoiceEvent } from './lib/voiceState'


const safeGetItem = (key, fallback = '') => {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
const safeSetItem = (key, value) => {
  try { localStorage.setItem(key, value) } catch { /* quota / privacy mode */ }
}

// Human label per scheduled-alert kind (backend sends `kind`).
const ALERT_KIND_LABEL = {
  timer_due: 'Timer',
  reminder_due: 'Reminder',
  task_deadline: 'Task deadline',
  focus_end: 'Focus session',
  ai_radar_digest: 'AI Radar',
}

export default function App() {
  const [theme, setTheme] = useState('sci-fi-hud')
  const [health, setHealth] = useState('Checking...')
  const [showSettings, setShowSettings] = useState(false)
  const [setupComplete, setSetupComplete] = useState(() => safeGetItem('jarvisSetupComplete') === 'true')

  const [userName, setUserName] = useState(() => safeGetItem('jarvisUserName', ''))
  // null = send no override so the backend's configured LLM_PROVIDER (.env)
  // governs. Defaulting the UI to 'gemini' here used to silently route every
  // fresh install to the cloud even when the user configured a local model.
  const [llmProvider, setLlmProvider] = useState(() => safeGetItem('jarvisLlmProvider', null))
  const [weatherCity, setWeatherCity] = useState(() => safeGetItem('jarvisWeatherCity', 'London'))
  const [cryptoCoins, setCryptoCoins] = useState(() => safeGetItem('jarvisCryptoCoins', 'bitcoin,ethereum'))

  const [isVisible, setIsVisible] = useState(true)
  const [playSummonSound, setPlaySummonSound] = useState(() => safeGetItem('jarvisSummonSound') === 'true')
  const [autoLaunch, setAutoLaunch] = useState(false)

  const [cameraEnabled, setCameraEnabled] = useState(() => safeGetItem('jarvisCameraEnabled') === 'true')
  const [wakeWordEnabled, setWakeWordEnabled] = useState(() => safeGetItem('jarvisWakeWordEnabled') !== 'false')
  const [micDevices, setMicDevices] = useState([])          // available input devices
  const [selectedMic, setSelectedMic] = useState('')        // currently selected mic name
  const [isExpanded, setIsExpanded] = useState(false)
  const [activeModule, setActiveModule] = useState(null)

  const rotationAccRef = useRef({ x: 0, y: 0 })
  const zoomAccRef = useRef(0)
  const [pointerCoords, setPointerCoords] = useState(null)
  const [resetTrigger, setResetTrigger] = useState(0)
  const [cameraFps, setCameraFps] = useState(30)

  // ── Voice WebSocket (wake word) — owned here, NOT in AIBrain ──
  // AIBrain unmounts while the dashboard is hidden, so a listener there could
  // never hear "Hey Jarvis" to summon it. From App the socket stays connected
  // even when hidden. Conversation state also lives in App (useChat), so a
  // transcript that arrives while hidden is processed and remembered.
  // `voiceUI.micListening` tracks the BACKEND mic state (structured "mic"
  // events), never mere WebSocket connectivity — so "Voice offline" is honest
  // when the model failed to load or the user switched the wake word off.
  const [voiceUI, voiceDispatch] = useReducer(reduceVoiceEvent, initialVoiceUI)

  // Chat engine (messages, send, TTS, persistence) — owned by App so it
  // survives AIBrain remounts on theme/overlay/settings changes.
  const chat = useChat({ userName, provider: llmProvider })

  // Stable indirection so the voice WS effect never re-subscribes and never
  // holds a stale send callback.
  const sendMessageRef = useRef(chat.sendMessage)
  useEffect(() => { sendMessageRef.current = chat.sendMessage }, [chat.sendMessage])

  const dispatchTranscript = useCallback((text) => {
    const t = typeof text === 'string' ? text.trim() : ''
    if (t) sendMessageRef.current(t, { source: 'voice' })
  }, [])

  const audioCtxRef = useRef(null)

  const playProceduralBeep = useCallback(() => {
    if (!playSummonSound) return
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)()
      }
      const ctx = audioCtxRef.current
      if (ctx.state === 'suspended') ctx.resume()
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(880, ctx.currentTime)
      osc.frequency.exponentialRampToValueAtTime(1760, ctx.currentTime + 0.1)
      gain.gain.setValueAtTime(0, ctx.currentTime)
      gain.gain.linearRampToValueAtTime(0.1, ctx.currentTime + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start()
      osc.stop(ctx.currentTime + 0.2)
    } catch { /* silent */ }
  }, [playSummonSound])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  // Prevent white flash: dark html in browser, transparent in Electron
  useEffect(() => {
    if (window.electronAPI) document.documentElement.classList.add('electron')
  }, [])

  // Keep latest beep fn in a ref so the visibility listener doesn't re-subscribe on sound toggle
  const beepRef = useRef(playProceduralBeep)
  useEffect(() => { beepRef.current = playProceduralBeep }, [playProceduralBeep])

  // Keep showSettings in ref for stable Escape handler
  const showSettingsRef = useRef(showSettings)
  useEffect(() => { showSettingsRef.current = showSettings }, [showSettings])

  // Zoom via NeuralCosmos HUD buttons — push to the shared accumulator
  useEffect(() => {
    const onZoomBtn = (e) => {
      const d = e.detail ?? 0
      zoomAccRef.current += d
    }
    window.addEventListener('jarvis-zoom', onZoomBtn)
    return () => window.removeEventListener('jarvis-zoom', onZoomBtn)
  }, [])

  // Wake-word WebSocket — reconnects forever, survives dashboard hide/show
  useEffect(() => {
    let ws = null
    let reconnectTimer = null
    let isUnmounted = false

    const connect = () => {
      ws = new WebSocket('ws://localhost:8000/ws/voice')

      // No "active" claim on open: the backend immediately sends the real mic
      // state as a "mic" event, which is what drives the indicator.
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'wake') {
            if (window.electronAPI) window.electronAPI.showWindow()
            setIsVisible(true)
          }
          if (data.type === 'transcript' && data.text && data.text.trim()) {
            dispatchTranscript(data.text.trim())
          }
          // The reducer owns every state transition (idle/wake/listening/
          // processing, mic truth, terminal notes) — see lib/voiceState.js.
          voiceDispatch(data)
        } catch (e) {
          console.error('[VOICE WS] Parse error:', e)
        }
      }

      ws.onclose = () => {
        voiceDispatch({ type: 'ws_close' })
        if (!isUnmounted) reconnectTimer = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        try { ws.close() } catch { /* already closing */ }
      }
    }

    connect()

    return () => {
      isUnmounted = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [dispatchTranscript])

  // ── Alert notifications (timer / reminder / task deadline / focus end) ──
  // Owned here so the /ws/alerts socket stays connected while the overlay is
  // hidden. The OS notification is the channel that's visible then; the in-app
  // toast is the on-screen fallback when the dashboard is shown.
  const [toasts, setToasts] = useState([])
  const toastIdRef = useRef(0)

  const pushToast = useCallback((toast) => {
    const id = ++toastIdRef.current
    setToasts(prev => [...prev, { id, ...toast }].slice(-5))
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 9000)
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const raiseAlert = useCallback((alert) => {
    const label = ALERT_KIND_LABEL[alert.kind] || 'Alert'
    const title = `V.E.G.A. — ${label}`
    const body = alert.message || 'You have a notification.'
    try {
      if (typeof Notification !== 'undefined') {
        if (Notification.permission === 'granted') {
          new Notification(title, { body })
        } else if (Notification.permission !== 'denied') {
          Notification.requestPermission().then(p => {
            if (p === 'granted') { try { new Notification(title, { body }) } catch { /* noop */ } }
          }).catch(() => { /* permission prompt unavailable */ })
        }
      }
    } catch { /* notifications unsupported in this context */ }
    pushToast({ title, body })
  }, [pushToast])

  // Alert WebSocket — reconnects forever, survives dashboard hide/show.
  useEffect(() => {
    let ws = null
    let reconnectTimer = null
    let isUnmounted = false

    const connect = () => {
      ws = new WebSocket('ws://localhost:8000/ws/alerts')
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'alert' && data.alert) raiseAlert(data.alert)
        } catch (e) {
          console.error('[ALERT WS] Parse error:', e)
        }
      }
      ws.onclose = () => { if (!isUnmounted) reconnectTimer = setTimeout(connect, 3000) }
      ws.onerror = () => { try { ws.close() } catch { /* already closing */ } }
    }

    connect()

    return () => {
      isUnmounted = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [raiseAlert])

  // Ask for notification permission once up front (best effort; re-asked lazily
  // on the first alert if still undecided) so hidden-overlay alerts can surface.
  useEffect(() => {
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        Notification.requestPermission().catch(() => { /* ignore */ })
      }
    } catch { /* unsupported */ }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetch('http://localhost:8000/health', { signal: controller.signal })
      .then(res => res.json())
      .then(data => setHealth(data.status))
      .catch(() => { if (!controller.signal.aborted) setHealth('Offline') })

    // Re-sync the persisted wake-word preference to the backend on launch
    if (safeGetItem('jarvisWakeWordEnabled') === 'false') postVoiceEnabled(false)

    if (window.electronAPI) {
      window.electronAPI.getTheme().then(t => { if (t) setTheme(t) }).catch(() => {})
      window.electronAPI.getAutoLaunch().then(enabled => setAutoLaunch(enabled)).catch(() => {})

      const cleanupTheme = window.electronAPI.onThemeChanged((newTheme) => {
        setTheme(newTheme)
      })

      // Listen for IPC toggle from hotkey or blur (uses ref to avoid re-subscribing)
      const cleanupVis = window.electronAPI.onToggleVisibility((visible) => {
        if (visible) {
          setIsVisible(true)
          beepRef.current?.()
        } else {
          setIsVisible(false)
        }
      })

      const handleKeyDown = (e) => {
        if (e.key === 'Escape') {
          if (showSettingsRef.current) setShowSettings(false)
          else setIsVisible(false)
        }
      }
      window.addEventListener('keydown', handleKeyDown)

      return () => {
        controller.abort()
        cleanupTheme?.()
        cleanupVis?.()
        window.removeEventListener('keydown', handleKeyDown)
      }
    }

    // Browser-only Escape: only close settings, never hide app to avoid white screen
    const handleKeyDownBrowser = (e) => {
      if (e.key === 'Escape' && showSettingsRef.current) setShowSettings(false)
    }
    window.addEventListener('keydown', handleKeyDownBrowser)

    return () => {
      controller.abort()
      window.removeEventListener('keydown', handleKeyDownBrowser)
    }
  }, [])

  // When exit animation completes, tell Electron to physically hide the window.
  // In browser (no electronAPI) we must NOT stay hidden — restore visibility to avoid blank white screen.
  const handleExitComplete = () => {
    if (window.electronAPI) {
      window.electronAPI.hideWindowNow()
    } else {
      // Browser fallback: never leave UI hidden (X should not cause white screen when testing in browser)
      setIsVisible(true)
    }
  }

  const handleThemeChange = (newTheme) => {
    setTheme(newTheme)
    if (window.electronAPI) window.electronAPI.switchTheme(newTheme)
  }

  const handleLlmProviderChange = (newProvider) => {
    setLlmProvider(newProvider)
    // Clicking the active engine again clears the override and returns to
    // whatever backend/.env configures — the ₹0 default must stay reachable.
    try {
      if (newProvider) localStorage.setItem('jarvisLlmProvider', newProvider)
      else localStorage.removeItem('jarvisLlmProvider')
    } catch { /* quota / privacy mode */ }
  }

  const handleToggleSound = () => {
    const next = !playSummonSound
    setPlaySummonSound(next)
    safeSetItem('jarvisSummonSound', next.toString())
  }

  const handleToggleAutoLaunch = () => {
    const next = !autoLaunch
    setAutoLaunch(next)
    if (window.electronAPI) window.electronAPI.setAutoLaunch(next)
  }

  const handleToggleCamera = () => {
    const next = !cameraEnabled
    setCameraEnabled(next)
    safeSetItem('jarvisCameraEnabled', next.toString())
  }

  // Toggling wake word off closes the backend mic — classic-Bluetooth headsets
  // then leave Hands-Free Profile and their audio playback quality recovers.
  const postVoiceEnabled = (enabled) => {
    fetch('http://localhost:8000/api/voice/enabled', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    }).catch(() => { /* backend offline — nothing to sync */ })
  }

  const handleToggleWakeWord = () => {
    const next = !wakeWordEnabled
    setWakeWordEnabled(next)
    safeSetItem('jarvisWakeWordEnabled', next.toString())
    postVoiceEnabled(next)
  }

  // Fetch input devices from backend when settings panel opens
  const fetchMicDevices = () => {
    fetch('http://localhost:8000/api/voice/devices')
      .then(r => r.json())
      .then(data => {
        setMicDevices(data.devices || [])
        setSelectedMic(data.current || '')
      })
      .catch(() => { /* backend offline */ })
  }

  const handleMicChange = (e) => {
    const name = e.target.value
    setSelectedMic(name)
    fetch('http://localhost:8000/api/voice/device', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    }).catch(() => { /* backend offline */ })
  }

  // ── Expanded (full-screen) state — native IPC in Electron, Fullscreen API in browser ──
  useEffect(() => {
    if (window.electronAPI) {
      window.electronAPI.getMaximizeState?.().then(setIsExpanded).catch(() => {})
      return window.electronAPI.onMaximizeChanged?.(setIsExpanded)
    }
    const onFsChange = () => setIsExpanded(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onFsChange)
    return () => document.removeEventListener('fullscreenchange', onFsChange)
  }, [])

  const handleMaximize = () => {
    if (window.electronAPI) {
      window.electronAPI.maximizeWindow()
      return
    }
    if (document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {})
    } else {
      document.documentElement.requestFullscreen?.().catch(() => {})
    }
  }

  const handleWizardComplete = (data) => {
    setUserName(data.userName)
    setWeatherCity(data.city)
    setLlmProvider(data.llm)
    handleThemeChange(data.theme)
    setSetupComplete(true)
  }

  // Both AIBrain instances (Neural Cosmos module view + standard layout) render
  // the same App-owned conversation, so switching themes never loses messages.
  const aiBrainProps = {
    messages: chat.messages,
    isThinking: chat.isThinking,
    isMuted: chat.isMuted,
    isSpeaking: chat.isSpeaking,
    voiceState: voiceUI.voiceState,
    wakeWordActive: voiceUI.micListening,
    voiceNote: voiceUI.statusNote,
    onSend: (text) => chat.sendMessage(text, { source: 'chat' }),
    onToggleMute: () => chat.setIsMuted(m => !m),
    onAppendMessage: chat.appendMessage,
    onClearHistory: chat.clearHistory,
  }

  return (
    <AnimatePresence onExitComplete={handleExitComplete}>
      {isVisible && (
        <motion.div
          key="app-root"
          className="h-screen w-screen flex flex-col box-border p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.12, ease: 'easeOut' }}
        >
          {/* First-run Setup Wizard */}
          {!setupComplete && (
            <SetupWizard onComplete={handleWizardComplete} />
          )}

          {/* Main Dashboard Shell */}
          {setupComplete && (
            <div className="jarvis-shell flex-1 flex flex-col min-h-0 overflow-hidden shadow-2xl">

              {/* Header */}
              <header className="jarvis-header drag-region flex shrink-0 items-center justify-between p-3 select-none z-50 relative">
                <div className="flex items-center gap-3">
                  <div className="status-dot"></div>
                  <h1 className="text-xl font-bold tracking-widest m-0">V.E.G.A.</h1>
                  <span className="text-xs opacity-70 ml-2">[{health}]</span>
                  {theme === 'neural-cosmos' && cameraEnabled && isVisible && (
                    <div className="flex items-center gap-1 text-xs text-[#00ff66] animate-pulse bg-[#00ff66]/10 px-2 py-1 rounded">
                      <Camera size={12} />
                      <span>Tracking Active ({Math.round(cameraFps)} FPS)</span>
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 no-drag" style={{ WebkitAppRegion: 'no-drag' }}>
                  <button
                    className={`btn p-1 transition-transform hover:scale-110 active:scale-95 ${showSettings ? 'bg-[var(--accent)] text-black border-[var(--accent)]' : ''}`}
                    style={{ WebkitAppRegion: 'no-drag' }}
                    onClick={(e) => { e.stopPropagation(); setShowSettings(s => !s) }}
                    title={showSettings ? "Close Settings" : "Settings"}
                    aria-label="Settings"
                  >
                    <Settings size={18} />
                  </button>
                  <button
                    className="btn p-1 transition-transform hover:scale-110 active:scale-95"
                    style={{ WebkitAppRegion: 'no-drag' }}
                    onClick={(e) => { e.stopPropagation(); handleMaximize() }}
                    title={isExpanded ? "Restore size" : "Maximize to full screen"}
                    aria-label={isExpanded ? "Restore dashboard size" : "Maximize dashboard to full screen"}
                  >
                    {isExpanded ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
                  </button>
                  <button
                    className="btn p-1 transition-transform hover:scale-110 active:scale-95 hover:bg-red-500/20 hover:border-red-500 hover:text-red-400"
                    style={{ WebkitAppRegion: 'no-drag' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (showSettings) setShowSettings(false)
                      else if (window.electronAPI) setIsVisible(false)
                      else setShowSettings(false)
                    }}
                    title={showSettings ? "Close Settings" : window.electronAPI ? "Hide Dashboard (Ctrl+Space to restore)" : "Close Settings"}
                    aria-label={showSettings ? "Close settings" : "Hide dashboard"}
                  >
                    <X size={18} />
                  </button>
                </div>
              </header>

              {/* Settings Panel — solid bg, no blur to avoid white flash on transparent window */}
              <AnimatePresence initial={false}>
                {showSettings && (
                  <motion.div
                    key="settings"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15, ease: 'easeInOut' }}
                    className="overflow-hidden border-b border-[var(--border-color)]/30"
                    style={{ backgroundColor: 'rgba(8, 12, 24, 0.98)' }}
                    onAnimationStart={() => { if (showSettings) fetchMicDevices() }}
                  >
                    <div className="p-4 flex flex-col gap-4">
                      <div className="flex items-center justify-between">
                        <h3 className="text-xs font-bold tracking-[0.2em] opacity-60">SETTINGS</h3>
                        <button
                          onClick={() => setShowSettings(false)}
                          className="text-xs px-2 py-1 border border-current/30 hover:bg-white/10 transition-colors flex items-center gap-1"
                        >
                          <X size={12} /> Close
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-6 items-center">

                        <div className="flex items-center gap-2">
                          <label className="text-sm font-semibold">Theme:</label>
                          <div className="toggle-group">
                            {['sci-fi-hud', 'glass', 'terminal', 'neural-cosmos'].map(t => (
                              <button
                                key={t}
                                className={`toggle-btn ${theme === t ? 'active' : ''}`}
                                onClick={() => handleThemeChange(t)}
                              >
                                {t.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="flex items-center gap-2">
                          <label className="text-sm font-semibold">LLM Engine:</label>
                          <div className="toggle-group">
                            {['gemini', 'ollama'].map(p => (
                              <button
                                key={p}
                                className={`toggle-btn ${llmProvider === p ? 'active' : ''}`}
                                onClick={() => handleLlmProviderChange(llmProvider === p ? null : p)}
                              >
                                {p === 'gemini' ? 'Gemini API' : 'Local Ollama'}
                              </button>
                            ))}
                          </div>
                          {!llmProvider && (
                            <span className="text-xs opacity-70">backend default (backend/.env)</span>
                          )}
                        </div>

                        <div className="flex items-center gap-4 border-l border-current/20 pl-4">
                          <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer">
                            <input type="checkbox" checked={playSummonSound} onChange={handleToggleSound} className="accent-[var(--accent)]" />
                            Summon Sound
                          </label>
                          <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer">
                            <input type="checkbox" checked={autoLaunch} onChange={handleToggleAutoLaunch} className="accent-[var(--accent)]" />
                            Launch on Startup
                          </label>
                          <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer" title="Enable hand gesture tracking for Neural Cosmos theme">
                            <input type="checkbox" checked={cameraEnabled} onChange={handleToggleCamera} className="accent-[var(--accent)]" />
                            Gesture Camera
                          </label>
                          <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer" title="Always-on 'Hey Jarvis' wake word. OFF closes the mic — Bluetooth headsets then keep full music quality (keeping the mic open forces them into Hands-Free mode, which degrades playback).">
                            <input type="checkbox" checked={wakeWordEnabled} onChange={handleToggleWakeWord} className="accent-[var(--accent)]" />
                            Wake Word
                          </label>
                        </div>

                      </div>

                      <div className="flex gap-4">
                        <div className="flex items-center gap-2">
                          <label className="text-sm font-semibold">User Name:</label>
                          <input
                            type="text"
                            className="theme-select text-sm p-1 max-w-[120px] bg-black/20 border border-current/30"
                            placeholder="e.g. Sir"
                            value={userName}
                            onChange={(e) => { setUserName(e.target.value); safeSetItem('jarvisUserName', e.target.value) }}
                          />
                        </div>
                        <div className="flex items-center gap-2">
                          <label className="text-sm font-semibold">City:</label>
                          <input
                            type="text"
                            className="theme-select text-sm p-1 max-w-[100px] bg-black/20 border border-current/30"
                            placeholder="e.g. London"
                            value={weatherCity}
                            onChange={(e) => { setWeatherCity(e.target.value); safeSetItem('jarvisWeatherCity', e.target.value) }}
                          />
                        </div>
                        <div className="flex items-center gap-2">
                          <label className="text-sm font-semibold">Coins:</label>
                          <input
                            type="text"
                            className="theme-select text-sm p-1 max-w-[140px] bg-black/20 border border-current/30"
                            placeholder="bitcoin,ethereum"
                            value={cryptoCoins}
                            onChange={(e) => { setCryptoCoins(e.target.value); safeSetItem('jarvisCryptoCoins', e.target.value) }}
                          />
                        </div>

                        {/* Microphone selector */}
                        {micDevices.length > 0 && (
                          <div className="flex items-center gap-2">
                            <label className="text-sm font-semibold" title="Pick which microphone the wake word listener uses. Takes effect immediately — no restart needed.">🎤 Mic:</label>
                            <select
                              id="mic-device-select"
                              className="theme-select text-sm p-1 bg-black/20 border border-current/30 max-w-[200px] truncate"
                              value={selectedMic}
                              onChange={handleMicChange}
                              title={selectedMic || 'System Default'}
                            >
                              <option value="">System Default</option>
                              {micDevices.map(d => (
                                <option key={d.id} value={d.name}>{d.name}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Main Content */}
              {theme === 'neural-cosmos' ? (
                <main className="flex-1 min-h-0 relative overflow-hidden bg-[#020205]">
                  <HandGestureController 
                    isActive={cameraEnabled && isVisible && theme === 'neural-cosmos'}
                    rotationAccRef={rotationAccRef}
                    zoomAccRef={zoomAccRef}
                    onPointerMove={(x, y) => setPointerCoords(x === null ? null : {x, y, clicked: false})}
                    onSelect={(x, y) => setPointerCoords({x, y, clicked: true})}
                    onReset={() => setResetTrigger(v => v + 1)}
                    onFpsUpdate={setCameraFps}
                  />

                  {pointerCoords && !activeModule && theme === 'neural-cosmos' && (
                    <div 
                      className="absolute w-4 h-4 rounded-full bg-[#00f0ff] pointer-events-none z-50 transition-all duration-75 shadow-[0_0_15px_#00f0ff]"
                      style={{ 
                        left: `${(pointerCoords.x + 1) / 2 * 100}%`,
                        top: `${(-pointerCoords.y + 1) / 2 * 100}%`,
                        transform: pointerCoords.clicked ? 'scale(2) translate(-25%, -25%)' : 'scale(1) translate(-50%, -50%)',
                        opacity: pointerCoords.clicked ? 0 : 0.8
                      }}
                    />
                  )}
                  
                  {activeModule ? (
                    <div className="absolute inset-0 z-10 flex flex-col p-6 bg-black/80 backdrop-blur-md">
                      <button 
                        className="btn mb-4 w-fit px-4 py-2 border border-[var(--accent)] text-[var(--accent)]"
                        onClick={() => setActiveModule(null)}
                      >
                        ← Return to Hub
                      </button>
                      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
                        {activeModule === 'AIBrain' && (
                          <AIBrain {...aiBrainProps} />
                        )}
                        {activeModule === 'ProductivityHub' && <ProductivityHub />}
                        {activeModule === 'SystemMonitor' && <SystemMonitor />}
                        {activeModule === 'LiveFeeds' && <LiveFeeds city={weatherCity} cryptoCoins={cryptoCoins} />}
                        {activeModule === 'FocusMode' && <div className="text-center mt-20 text-2xl">Focus Mode Active</div>}
                      </div>
                    </div>
                  ) : (
                    <div className="absolute inset-0 z-0">
                      <NeuralCosmos 
                        onSelectModule={(id) => setActiveModule(id)}
                        pointerCoords={pointerCoords}
                        rotationAccRef={rotationAccRef}
                        zoomAccRef={zoomAccRef}
                        resetTrigger={resetTrigger}
                        gesturesActive={cameraEnabled && cameraFps > 0}
                      />
                    </div>
                  )}
                </main>
              ) : (
                <main className="dashboard-layout custom-scrollbar">
                  <div className="dashboard-column custom-scrollbar" role="region" aria-label="Assistant and productivity" tabIndex={0}>
                    <div className="dashboard-chat">
                      <AIBrain {...aiBrainProps} />
                    </div>
                    <ProductivityHub />
                  </div>
                  <div className="dashboard-column custom-scrollbar" role="region" aria-label="System and feeds" tabIndex={0}>
                    <SystemMonitor />
                    <LiveFeeds city={weatherCity} cryptoCoins={cryptoCoins} />
                  </div>
                </main>
              )}

            </div>
          )}

          {/* Alert toasts — on-screen fallback for /ws/alerts notifications.
              Rendered above all modules; OS notifications cover the hidden case. */}
          <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 no-drag" style={{ WebkitAppRegion: 'no-drag' }}>
            <AnimatePresence>
              {toasts.map(t => (
                <motion.div
                  key={t.id}
                  initial={{ opacity: 0, x: 40 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 40 }}
                  transition={{ duration: 0.15 }}
                  className="flex items-start gap-3 px-4 py-3 rounded border border-[var(--accent)]/40 bg-black/85 backdrop-blur shadow-lg max-w-xs"
                >
                  <Bell size={16} className="text-[var(--accent)] mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold tracking-wider text-[var(--accent)] truncate">{t.title}</div>
                    <div className="text-sm mt-0.5 break-words">{t.body}</div>
                  </div>
                  <button
                    onClick={() => dismissToast(t.id)}
                    className="opacity-50 hover:opacity-100 shrink-0"
                    aria-label="Dismiss notification"
                  >
                    <X size={14} />
                  </button>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
