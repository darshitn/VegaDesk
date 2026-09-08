import { useState } from 'react'
import { motion } from 'framer-motion'
import { Cpu } from 'lucide-react'

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(1)
  const [formData, setFormData] = useState({
    userName: 'Sir',
    city: 'London',
    theme: 'sci-fi-hud',
    mode: 'Hotkey Overlay',
    llm: 'gemini'
  })

  const updateForm = (key, value) => {
    setFormData(prev => ({ ...prev, [key]: value }))
  }

  const handleNext = () => {
    if (step < 3) setStep(step + 1)
    else handleComplete()
  }

  const handleComplete = () => {
    try {
      localStorage.setItem('jarvisSetupComplete', 'true')
      localStorage.setItem('jarvisUserName', formData.userName.trim() || 'Sir')
      localStorage.setItem('jarvisWeatherCity', formData.city.trim() || 'London')
      localStorage.setItem('jarvisLlmProvider', formData.llm)
    } catch { /* privacy mode */ }
    
    // The main App component will read these and apply them
    if (window.electronAPI) {
      window.electronAPI.switchTheme(formData.theme)
      window.electronAPI.switchMode(formData.mode)
    }
    
    onComplete(formData)
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
            
            <label className="flex flex-col gap-2 text-sm font-semibold">
              LLM Provider:
              <div className="grid grid-cols-2 gap-2">
                <button 
                  onClick={() => updateForm('llm', 'gemini')}
                  className={`p-3 border flex flex-col items-center gap-1 transition-all ${formData.llm === 'gemini' ? 'border-[var(--accent)]/20 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current opacity-60 hover:opacity-100'}`}
                >
                  <span className="font-bold">Gemini API</span>
                  <span className="text-xs opacity-70 font-normal">Cloud (Requires .env key)</span>
                </button>
                <button 
                  onClick={() => updateForm('llm', 'ollama')}
                  className={`p-3 border flex flex-col items-center gap-1 transition-all ${formData.llm === 'ollama' ? 'border-[var(--accent)]/20 bg-[var(--accent)]/20 text-[var(--accent)]' : 'border-current opacity-60 hover:opacity-100'}`}
                >
                  <span className="font-bold">Ollama</span>
                  <span className="text-xs opacity-70 font-normal">Local (Fully offline)</span>
                </button>
              </div>
            </label>
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
                onClick={() => setStep(s => s - 1)}
                className="px-4 py-2 border border-current/30 text-sm hover:bg-white/10 transition-colors uppercase tracking-widest"
              >
                Back
              </button>
            )}
            <button
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
