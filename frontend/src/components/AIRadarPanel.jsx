import { useState, useEffect, useCallback } from 'react'
import { Radar, RefreshCw, ExternalLink, AlertTriangle, CheckCircle2, HelpCircle, Clock } from 'lucide-react'
import { getApiBase, verifiedFetch } from '../lib/apiConfig'

const API = getApiBase()

const CATEGORY_LABEL = {
  local_model: 'Local model',
  free_api_model: 'Free API model',
  model_release: 'Model release',
  credit_offer: 'Credit offer',
  provider_change: 'Provider change',
  other: 'AI',
}

function openLink(url) {
  if (!url) return
  if (window.electronAPI && window.electronAPI.openExternal) {
    window.electronAPI.openExternal(url)
  } else {
    window.open(url, '_blank')
  }
}

function agoLabel(seconds) {
  if (seconds == null) return 'never'
  if (seconds < 60) return 'just now'
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

// A free hosted model is not a free token/credit grant. Only surface "free"
// language when the offer is genuinely current; expired/unconfirmed say so.
function offerBadge(item) {
  const status = item.verification_status
  if (status === 'expired') {
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-400/30">expired offer</span>
  }
  const offer = item.offer
  const isFreeModel = item.category === 'free_api_model'
  if (!offer && !isFreeModel) return null
  if (isFreeModel) {
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-300 border border-green-400/30">free model</span>
  }
  if (offer?.kind === 'credit_offer' || item.category === 'credit_offer') {
    const billing = offer?.billing_required
    const label = billing === true ? 'promo (card required)'
      : billing === false ? 'promo (no card)'
      : 'promo (billing unconfirmed)'
    return <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-400/30">{label}</span>
  }
  return null
}

function verifyIcon(status) {
  if (status === 'verified') return <CheckCircle2 size={12} className="text-green-400 flex-shrink-0 mt-0.5" title="verified from an official source" />
  if (status === 'expired') return <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" title="offer expired" />
  return <HelpCircle size={12} className="text-yellow-400/80 flex-shrink-0 mt-0.5" title="candidate — not confirmed as a major launch" />
}

export default function AIRadarPanel() {
  const [state, setState] = useState(null)
  const [error, setError] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/ai-radar`, { signal: AbortSignal.timeout(10000) })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setState(await res.json())
      setError(false)
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      const res = await verifiedFetch(`${API}/api/ai-radar/refresh`, { method: 'POST', signal: AbortSignal.timeout(60000) })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setState(data.state || data)
      setError(false)
    } catch {
      setError(true)
    } finally {
      setRefreshing(false)
    }
  }

  const markRead = async (id) => {
    try {
      await verifiedFetch(`${API}/api/ai-radar/items/${id}/read`, { method: 'POST', signal: AbortSignal.timeout(10000) })
    } catch { /* non-critical */ }
  }

  const items = state?.items || []
  const freshness = state?.freshness
  const lastRun = state?.last_run

  return (
    <div className="p-3 bg-black/20 rounded-lg border border-current/10">
      <div className="flex items-center gap-2 mb-2 opacity-80 border-b border-current/10 pb-1">
        <Radar size={14} className="text-[var(--accent)]" />
        <span className="text-xs uppercase tracking-wider">AI Radar</span>
        <span className="ml-auto flex items-center gap-1 text-[10px] opacity-60">
          <Clock size={10} />
          {freshness ? `checked ${agoLabel(freshness.age_seconds)}` : 'not run yet'}
        </span>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          title="Refresh now"
          className="p-1 rounded hover:bg-current/10 disabled:opacity-40 transition-colors"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && !state ? (
        <div className="text-red-400 text-xs flex items-center gap-1"><AlertTriangle size={12}/> Radar unavailable</div>
      ) : !state ? (
        <div className="text-xs opacity-50 animate-pulse">Scanning sources...</div>
      ) : items.length === 0 ? (
        <div className="text-xs opacity-50">
          No items yet. {lastRun?.status === 'failed' ? 'Last run could not reach any source (offline?).' : 'Run a refresh to check public sources.'}
        </div>
      ) : (
        <ul className="space-y-2 max-h-64 overflow-y-auto pr-1">
          {items.map((item) => (
            <li key={item.id} className="text-sm">
              <button
                onClick={() => { openLink(item.url); markRead(item.id) }}
                className="text-left w-full hover:text-[var(--accent)] transition-colors flex items-start gap-2 group"
              >
                <ExternalLink size={12} className="opacity-0 group-hover:opacity-100 mt-1 flex-shrink-0 transition-opacity" />
                {verifyIcon(item.verification_status)}
                <span className="flex-1 min-w-0">
                  <span className="line-clamp-2">{item.title}</span>
                  <span className="flex flex-wrap items-center gap-1.5 mt-1">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-current/10 opacity-70">
                      {CATEGORY_LABEL[item.category] || item.category}
                    </span>
                    {offerBadge(item)}
                    {item.publisher && <span className="text-[10px] opacity-50">{item.publisher}</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {freshness && freshness.status === 'partial' && (
        <div className="text-[10px] opacity-50 mt-2 flex items-center gap-1">
          <AlertTriangle size={10}/> Some sources failed last run; showing last known items.
        </div>
      )}
    </div>
  )
}
