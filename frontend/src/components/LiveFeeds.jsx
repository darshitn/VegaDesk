import { useState, useEffect, useCallback } from 'react'
import { Cloud, Newspaper, Bitcoin, AlertTriangle, TrendingUp, TrendingDown, ExternalLink, Activity } from 'lucide-react'

export default function LiveFeeds({ city, cryptoCoins }) {
  const [weather, setWeather] = useState(null)
  const [headlines, setHeadlines] = useState(null)
  const [crypto, setCrypto] = useState(null)
  
  const [weatherError, setWeatherError] = useState(false)
  const [headlinesError, setHeadlinesError] = useState(false)
  const [cryptoError, setCryptoError] = useState(false)

  const fetchFeeds = useCallback(async () => {
    // Weather
    try {
      const wRes = await fetch(`http://localhost:8000/api/feeds/weather?city=${encodeURIComponent(city)}`, { signal: AbortSignal.timeout(10000) })
      const wData = await wRes.json()
      if (wData.error) throw new Error(wData.error)
      setWeather(wData)
      setWeatherError(false)
    } catch {
      setWeatherError(true)
    }

    // Headlines
    try {
      const hRes = await fetch(`http://localhost:8000/api/feeds/headlines`, { signal: AbortSignal.timeout(10000) })
      const hData = await hRes.json()
      if (hData.error) throw new Error(hData.error)
      if (!Array.isArray(hData)) throw new Error('Invalid headlines format')
      setHeadlines(hData)
      setHeadlinesError(false)
    } catch {
      setHeadlinesError(true)
    }

    // Crypto
    try {
      const coinsParam = cryptoCoins?.trim() ? cryptoCoins.trim() : 'bitcoin,ethereum'
      const cRes = await fetch(`http://localhost:8000/api/feeds/crypto?coins=${encodeURIComponent(coinsParam)}`, { signal: AbortSignal.timeout(10000) })
      const cData = await cRes.json()
      if (cData.error) throw new Error(cData.error)
      setCrypto(cData)
      setCryptoError(false)
    } catch {
      setCryptoError(true)
    }
  }, [city, cryptoCoins])

  useEffect(() => {
    fetchFeeds()
    const interval = setInterval(fetchFeeds, 60000) // refresh every 1 minute
    return () => clearInterval(interval)
  }, [fetchFeeds])

  const handleLinkClick = (url) => {
    if (window.electronAPI && window.electronAPI.openExternal) {
      window.electronAPI.openExternal(url)
    } else {
      window.open(url, '_blank')
    }
  }

  // WMO Weather code mapping (simplified)
  const getWeatherIcon = (code) => {
    if (code === undefined) return <Cloud size={24} className="text-[var(--accent)]" />
    if (code <= 3) return <Cloud size={24} className="text-yellow-400 drop-shadow-[0_0_8px_rgba(250,204,21,0.6)]" /> // Sun/Partly cloudy
    if (code < 50) return <Cloud size={24} className="text-gray-400" /> // Fog
    return <Cloud size={24} className="text-blue-400 drop-shadow-[0_0_8px_rgba(96,165,250,0.6)]" /> // Rain/Snow
  }

  return (
    <div className="flex flex-col w-full max-w-3xl mx-auto rounded-lg border border-current/20 bg-black/10 backdrop-blur-sm overflow-hidden shadow-inner">
      <div className="flex items-center gap-2 p-3 border-b border-current/20 bg-black/20">
        <Activity size={18} className="text-[var(--accent)] hidden" />
        <Cloud size={18} className="text-[var(--accent)]" />
        <h2 className="text-sm font-semibold tracking-wider">DATA FEEDS</h2>
      </div>

      <div className="p-4 flex flex-col gap-4 overflow-y-auto">
        
        {/* Weather Feed */}
        <div className="p-3 bg-black/20 rounded-lg border border-current/10">
          <div className="flex items-center gap-2 mb-2 opacity-70 border-b border-current/10 pb-1">
            <Cloud size={14} />
            <span className="text-xs uppercase tracking-wider">Meteorological (Local)</span>
          </div>
          {weatherError ? (
            <div className="text-red-400 text-xs flex items-center gap-1"><AlertTriangle size={12}/> Failed to fetch weather</div>
          ) : !weather ? (
            <div className="text-xs opacity-50 animate-pulse">Scanning atmosphere...</div>
          ) : (
            <div className="flex items-center gap-4">
              {getWeatherIcon(weather.weathercode)}
              <div>
                <div className="text-xl font-bold tracking-tight">{weather.temperature}°C</div>
                <div className="text-xs opacity-70 uppercase tracking-widest">{weather.city}</div>
              </div>
            </div>
          )}
        </div>

        {/* Crypto Feed */}
        {cryptoCoins && (
          <div className="p-3 bg-black/20 rounded-lg border border-current/10">
            <div className="flex items-center gap-2 mb-2 opacity-70 border-b border-current/10 pb-1">
              <Bitcoin size={14} />
              <span className="text-xs uppercase tracking-wider">Digital Assets</span>
            </div>
            {cryptoError ? (
              <div className="text-red-400 text-xs flex items-center gap-1"><AlertTriangle size={12}/> Failed to fetch crypto</div>
            ) : !crypto ? (
              <div className="text-xs opacity-50 animate-pulse">Syncing blockchain...</div>
            ) : (
              <div className="flex flex-wrap gap-4">
                {Object.entries(crypto).map(([coin, data]) => (
                  <div key={coin} className="flex flex-col">
                    <span className="text-xs uppercase opacity-70">{coin}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold">${data.price != null ? Number(data.price).toLocaleString() : '—'}</span>
                      {data.change_24h == null ? (
                        <span className="text-xs opacity-50">—</span>
                      ) : data.change_24h > 0 ? (
                        <span className="text-green-400 text-xs flex items-center"><TrendingUp size={12}/> {data.change_24h?.toFixed(2)}%</span>
                      ) : (
                        <span className="text-red-400 text-xs flex items-center"><TrendingDown size={12}/> {Math.abs(data.change_24h)?.toFixed(2)}%</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Headlines Feed */}
        <div className="p-3 bg-black/20 rounded-lg border border-current/10 flex-1">
          <div className="flex items-center gap-2 mb-2 opacity-70 border-b border-current/10 pb-1">
            <Newspaper size={14} />
            <span className="text-xs uppercase tracking-wider">Global Net (Hacker News)</span>
          </div>
          {headlinesError ? (
            <div className="text-red-400 text-xs flex items-center gap-1"><AlertTriangle size={12}/> Failed to fetch headlines</div>
          ) : !headlines ? (
            <div className="text-xs opacity-50 animate-pulse">Decrypting packets...</div>
          ) : (
            <ul className="space-y-2">
              {headlines.map((hl, idx) => (
                <li key={idx} className="text-sm">
                  <button 
                    onClick={() => handleLinkClick(hl.url)}
                    className="text-left w-full hover:text-[var(--accent)] transition-colors flex items-start gap-2 group"
                  >
                    <ExternalLink size={12} className="opacity-0 group-hover:opacity-100 mt-1 flex-shrink-0 transition-opacity" />
                    <span className="line-clamp-2">{hl.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

      </div>
    </div>
  )
}
