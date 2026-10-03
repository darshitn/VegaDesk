import { useState } from 'react'
import { motion } from 'framer-motion'
import { Cpu } from 'lucide-react'

import { DEFAULT_SETUP_CONFIG, persistSetupChoices } from '../lib/setupConfig.js'
import { getApiBase } from '../lib/apiConfig.js'

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(1)
  const [formData, setFormData] = useState({ ...DEFAULT_SETUP_CONFIG })
  const [ollamaCheck, setOllamaCheck] = useState(null) // null | { loading: boolean, message: string, ready: boolean }

  const updateForm = (key, value) => {
    setFormData(prev => ({ ...prev, [key]: value }))
  }

  const handleNext = () => {
    if (step < 3) setStep(step + 1)
    else handleComplete()
  }

  const checkLocalOllama = async () => {
    setOllamaCheck({ loading: true, message: 'Checking localhost:11434...', ready: false })
    const controller = new AbortController()
    const tid = setTimeout(() => controller.abort(), 1500)
    try {
      const res = await fetch(`${getApiBase()}/health?diagnostics=1`, { signal: controller.signal })
      clearTimeout(tid)
      if (res.ok) {
        const data = await res.json()
        const pStatus = data.provider?.status
        if (pStatus === 'ready') {
          setOllamaCheck({ loading: false, message: `Ollama detected with model (${data.provider?.model || 'installed'}).`, ready: true })
        } else if (pStatus === 'missing_model') {
          setOllamaCheck({ loading: false, message: 'Ollama is reachable, but the default model is not downloaded yet.', ready: false })
        } else {
          setOllamaCheck({ loading: false, message: 'Ollama service is not reachable on localhost:11434.', ready: false })
        }
      } else {
        setOllamaCheck({ loading: false, message: 'Backend diagnostics unavailable.', ready: false })
      }
    } catch {
      clearTimeout(tid)
      setOllamaCheck({ loading: false, message: 'Ollama not detected on local port. You can still select it or use offline mode.', ready: false })
    }
  }

  const handleComplete = () => {
    persistSetupChoices(localStorage, formData)
    
    // The main App component will read these and apply them
    if (window.electronAPI) {
      window.electronAPI.switchTheme(formData.theme)
      window.electronAPI.switchMode(formData.mode)
    }
    
    onComplete({ ...formData, llm: formData.llm === 'backend' ? null : formData.llm })
  }

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.05 }}
      className="absolute inset-0 z-50 flex items-center justify-center p-6 bg-black/80 backdrop-blur-md"
    >
      <div className="w-full max-w-lg bg-black/60 border border-[var(--accent)]/30 rounded-xl p-8 shadow-2xl flex flex-col gap-6">
        
        <div className="flex items-center gap-3 justify-center mb-4">
          <Cpu size={32} className="text-[var(--accent)]" />
          <h1 className="text-2xl font-bold tracking-[0.2em] uppercase">V.E.G.A. Init</h1>
        </div>

        {/* Step 1: Profile */}
        {step === 1 && (
          <motion.div initial={{ x: 20, opacity: 0 }} animate={{ x: 0, opacity: 1 }} className="flex flex-col gap-4">
            <h2 className="text-sm uppercase tracking-widest opacity-70 border-b border-current/20 pb-2">Step 1: User Profile</h2>
            
            <label className="flex flex-col gap-1 text-sm font-semibold">
              Preferred Designation (Name):
              <input 
                type="text" 
                value={formData.userName} 
                onChange={e => updateForm('userName', e.target.value)}
                className="bg-black/40 border border-current/20 p-2 outline-none focus:border-[var(--accent)] transition-colors"
              />
            </label>

            <label className="flex flex-col gap-1 text-sm font-semibold">
              Location (City):
              <input 
                type="text" 
                value={formData.city} 
                onChange={e => updateForm('city', e.target.value)}
                className="bg-black/40 border border-current/20 p-2 outline-none focus:border-[var(--accent)] transition-colors"
              />
            </label>
          </motion.div>
        )}

        {/* Step 2: Aesthetics */}
        {step === 2 && (
          <motion.div initial={{ x: 20, opacity: 0 }} animate={{ x: 0, opacity: 1 }} className="flex flex-col gap-4">
            <h2 className="text-sm uppercase tracking-widest opacity-70 border-b border-current/20 pb-2">Step 2: Visual Interface</h2>
            
            <label className="flex flex-col gap-2 text-sm font-semibold">
              Primary Theme:
              <div className="grid grid-cols-2 gap-2">
                {['sci-fi-hud', 'glass', 'terminal', 'neural-cosmos'].map(t => (
                  <button 
                    key={t}
                    type="button"
                    onClick={() => updateForm('theme', t)}
                    className={`p-2 border transition-all ${formData.theme === t ? 'border-[var(--accent)]/20 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current opacity-60 hover:opacity-100'}`}
                  >
                    {t.split('-').map(w => w[0].toUpperCase() + w.slice(1)).join(' ')}
                  </button>
                ))}
              </div>
            </label>

            <label className="flex flex-col gap-2 text-sm font-semibold mt-2">
              Display Mode:
              <div className="grid grid-cols-2 gap-2">
                {['Hotkey Overlay', 'Pinned Desktop'].map(m => (
                  <button 
                    key={m}
                    type="button"
                    onClick={() => updateForm('mode', m)}
                    className={`p-2 border transition-all ${formData.mode === m ? 'border-[var(--accent)]/20 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current opacity-60 hover:opacity-100'}`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </label>
          </motion.div>
        )}

        {/* Step 3: Engine */}
        {step === 3 && (
          <motion.div initial={{ x: 20, opacity: 0 }} animate={{ x: 0, opacity: 1 }} className="flex flex-col gap-4">
            <h2 className="text-sm uppercase tracking-widest opacity-70 border-b border-current/20 pb-2">Step 3: Cognitive Engine</h2>
            
            <div className="flex flex-col gap-2 text-sm font-semibold">
              <span>Choose Operation Mode:</span>
              <div className="grid grid-cols-1 gap-2">
                {/* 1. Offline Deterministic */}
                <button 
                  type="button"
                  onClick={() => updateForm('llm', 'none')}
                  className={`p-3 border text-left flex flex-col gap-1 transition-all ${formData.llm === 'none' ? 'border-[var(--accent)]/40 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current/30 opacity-70 hover:opacity-100'}`}
                >
                  <span className="font-bold">Offline Assistant (Deterministic ₹0)</span>
                  <span className="text-xs opacity-80 font-normal">No API keys or model downloads. Tasks, timers, reminders, notes, and workspaces work 100% offline.</span>
                </button>

                {/* 2. Local Ollama */}
                <button 
                  type="button"
                  onClick={() => updateForm('llm', 'ollama')}
                  className={`p-3 border text-left flex flex-col gap-1 transition-all ${formData.llm === 'ollama' ? 'border-[var(--accent)]/40 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current/30 opacity-70 hover:opacity-100'}`}
                >
                  <span className="font-bold">Local Ollama</span>
                  <span className="text-xs opacity-80 font-normal">Private on-device AI. Connects to an existing local Ollama installation (e.g., llama3).</span>
                </button>

                {/* 3. Gemini Cloud */}
                <button
                  type="button"
                  onClick={() => updateForm('llm', 'gemini')}
                  className={`p-3 border text-left flex flex-col gap-1 transition-all ${formData.llm === 'gemini' ? 'border-[var(--accent)]/40 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current/30 opacity-70 hover:opacity-100'}`}
                >
                  <span className="font-bold">Gemini API (Cloud)</span>
                  <span className="text-xs opacity-80 font-normal">Cloud reasoning. Explicit opt-in requiring GEMINI_API_KEY in backend/.env.</span>
                </button>

                {/* 4. Backend Config */}
                <button
                  type="button"
                  onClick={() => updateForm('llm', 'backend')}
                  className={`p-3 border text-left flex flex-col gap-1 transition-all ${formData.llm === 'backend' ? 'border-[var(--accent)]/40 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current/30 opacity-70 hover:opacity-100'}`}
                >
                  <span className="font-bold">Server Default</span>
                  <span className="text-xs opacity-80 font-normal">Follow LLM_PROVIDER as configured on the server without client override.</span>
                </button>
              </div>

              {formData.llm === 'ollama' && (
                <div className="mt-2 p-2 border border-current/20 bg-black/40 rounded text-xs flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="opacity-70">Local Ollama Availability Check:</span>
                    <button
                      type="button"
                      onClick={checkLocalOllama}
                      disabled={ollamaCheck?.loading}
                      className="px-2 py-1 border border-current/40 text-[10px] uppercase tracking-wider hover:bg-white/10"
                    >
                      {ollamaCheck?.loading ? 'Checking...' : 'Check Status'}
                    </button>
                  </div>
                  {ollamaCheck && (
                    <span className={`text-[11px] ${ollamaCheck.ready ? 'text-green-400' : 'text-amber-400'}`}>
                      {ollamaCheck.message}
                    </span>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        )}

        {/* Navigation */}
        <div className="flex justify-between mt-4 pt-4 border-t border-current/20">
          <div className="flex gap-1 items-center">
            {[1,2,3].map(i => (
              <div key={i} className={`h-1 w-6 transition-all ${step >= i ? 'bg-[var(--accent)]' : 'bg-current opacity-20'}`}></div>
            ))}
          </div>
          <div className="flex gap-2">
            {step > 1 && (
              <button
                type="button"
                onClick={() => setStep(s => s - 1)}
                className="px-4 py-2 border border-current/30 text-sm hover:bg-white/10 transition-colors uppercase tracking-widest"
              >
                Back
              </button>
            )}
            <button
              type="button"
              onClick={handleNext}
              disabled={step === 1 && !formData.userName.trim()}
              className="px-6 py-2 bg-[var(--accent)] text-black font-bold tracking-widest hover:bg-[var(--accent)]/80 transition-colors uppercase text-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {step === 3 ? 'Initialize' : 'Next'}
            </button>
          </div>
        </div>

      </div>
    </motion.div>
  )
}
