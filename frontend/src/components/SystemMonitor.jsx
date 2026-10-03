import { useState, useEffect } from 'react'
import { Activity, Cpu, HardDrive, Zap, Wifi } from 'lucide-react'
import { getWsBase } from '../lib/apiConfig'

const formatBytes = (bytes, decimals = 1) => {
    if (!+bytes) return '0 B'
    const k = 1024
    const dm = decimals < 0 ? 0 : decimals
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const CircularGauge = ({ value, label, icon: Icon }) => {
    const radius = 36
    const circumference = 2 * Math.PI * radius
    const strokeDashoffset = circumference - (value / 100) * circumference

    return (
        <div className="flex flex-col items-center justify-center">
            <div className="relative flex items-center justify-center w-24 h-24">
                <svg className="w-full h-full transform -rotate-90">
                    <circle
                        className="text-current opacity-20"
                        strokeWidth="8"
                        stroke="currentColor"
                        fill="transparent"
                        r={radius}
                        cx="48"
                        cy="48"
                    />
                    <circle
                        className="text-[var(--accent)] drop-shadow-[0_0_4px_var(--accent)] transition-all duration-1000 ease-in-out"
                        strokeWidth="8"
                        strokeDasharray={circumference}
                        strokeDashoffset={strokeDashoffset}
                        strokeLinecap="round"
                        stroke="currentColor"
                        fill="transparent"
                        r={radius}
                        cx="48"
                        cy="48"
                    />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <Icon size={16} className="opacity-70 mb-1" />
                    <span className="text-xs font-bold">{Math.round(value)}%</span>
                </div>
            </div>
            <span className="text-[10px] tracking-wider uppercase opacity-70 mt-2 text-center">{label}</span>
        </div>
    )
}

export default function SystemMonitor() {
    const [stats, setStats] = useState(null)
    const [error, setError] = useState(false)

    useEffect(() => {
        let ws = null
        let connectTimer = null
        let isUnmounted = false

        const connect = () => {
            if (isUnmounted) return
            try {
                ws = new WebSocket(`${getWsBase()}/ws/system-stats`)
            } catch {
                setError(true)
                connectTimer = setTimeout(connect, 3000)
                return
            }
            ws.onmessage = (event) => {
                try {
                    setStats(JSON.parse(event.data))
                    setError(false)
                } catch (e) {
                    console.error('Failed to parse stats', e)
                }
            }
            ws.onerror = () => {
                setError(true)
            }
            ws.onclose = () => {
                if (!isUnmounted) {
                    setError(true)
                    connectTimer = setTimeout(connect, 3000)
                }
            }
        }

        connect()

        return () => {
            isUnmounted = true
            clearTimeout(connectTimer)
            if (ws) try { ws.close() } catch {}
        }
    }, [])

    if (error && !stats) {
        return (
            <div className="flex flex-col h-full w-full justify-center items-center opacity-50 p-4 border border-current/20 bg-black/10 rounded-lg">
                <Activity className="animate-pulse mb-2 text-red-500" />
                <span className="text-sm">Telemetry Offline</span>
            </div>
        )
    }

    if (!stats) {
        return (
            <div className="flex flex-col h-full w-full justify-center items-center opacity-50 p-4 border border-current/20 bg-black/10 rounded-lg">
                <Activity className="animate-pulse mb-2" />
                <span className="text-sm">Connecting to Core...</span>
            </div>
        )
    }

    return (
        <div className="flex flex-col w-full max-w-3xl mx-auto rounded-lg border border-current/20 bg-black/10 backdrop-blur-sm overflow-hidden shadow-inner">
            {/* Header */}
            <div className="flex items-center gap-2 p-3 border-b border-current/20 bg-black/20">
                <Activity size={18} className="text-[var(--accent)]" />
                <h2 className="text-sm font-semibold tracking-wider">SYSTEM TELEMETRY</h2>
            </div>

            <div className="p-6 grid grid-cols-2 gap-6 flex-1 overflow-y-auto">
                {/* Main Gauges */}
                <div className="flex justify-around items-center col-span-2 p-4 bg-black/20 rounded-lg border border-current/10">
                    <CircularGauge value={stats.cpu} label="CPU Core" icon={Cpu} />
                    <CircularGauge value={stats.ram} label="Memory" icon={Zap} />
                </div>

                {/* Network & Disk */}
                <div className="col-span-2 sm:col-span-1 space-y-4">
                    <div className="p-3 bg-black/20 rounded-lg border border-current/10">
                        <div className="flex items-center gap-2 mb-3 opacity-70">
                            <Wifi size={14} />
                            <span className="text-xs uppercase tracking-wider">Network I/O</span>
                        </div>
                        <div className="flex flex-wrap justify-between text-sm gap-x-2">
                            <span className="whitespace-nowrap">Up: <span className="text-[var(--accent)]">{formatBytes(stats.net_up)}/s</span></span>
                            <span className="whitespace-nowrap">Down: <span className="text-[var(--accent)]">{formatBytes(stats.net_down)}/s</span></span>
                        </div>
                    </div>

                    <div className="p-3 bg-black/20 rounded-lg border border-current/10">
                        <div className="flex items-center gap-2 mb-2 opacity-70">
                            <HardDrive size={14} />
                            <span className="text-xs uppercase tracking-wider">Storage</span>
                        </div>
                        <div className="w-full bg-black/40 rounded-full h-2.5 overflow-hidden">
                            <div className="bg-[var(--accent)] h-2.5 rounded-full transition-all duration-1000" style={{ width: `${stats.disk}%` }}></div>
                        </div>
                        <div className="mt-1 text-right text-xs opacity-70 truncate">{stats.disk}% Used</div>
                    </div>
                </div>

                {/* GPU & Battery */}
                <div className="col-span-2 sm:col-span-1 space-y-4">
                    {stats.gpu && (
                        <div className="p-3 bg-black/20 rounded-lg border border-current/10">
                            <div className="flex items-center gap-2 mb-2 opacity-70">
                                <Cpu size={14} />
                                <span className="text-xs uppercase tracking-wider truncate">GPU ({stats.gpu.name})</span>
                            </div>
                            <div className="flex flex-wrap justify-between text-sm mb-1 gap-x-2">
                                <span className="whitespace-nowrap">Load: <span className="text-[var(--accent)]">{Math.round(stats.gpu.load)}%</span></span>
                                <span className="whitespace-nowrap">VRAM: <span className="text-[var(--accent)]">{Math.round(stats.gpu.memory)}%</span></span>
                            </div>
                            <div className="flex justify-between text-xs opacity-70">
                                <span>Temp: {stats.gpu.temp}°C</span>
                            </div>
                        </div>
                    )}

                    {stats.battery !== null && (
                        <div className="p-3 bg-black/20 rounded-lg border border-current/10">
                            <div className="flex items-center gap-2 mb-2 opacity-70">
                                <Zap size={14} />
                                <span className="text-xs uppercase tracking-wider">Power Cell</span>
                            </div>
                            <div className="flex flex-wrap justify-between items-center text-sm gap-x-2">
                                <span className="whitespace-nowrap">Level: <span className={`font-bold ${stats.battery <= 20 ? 'text-red-500' : 'text-[var(--accent)]'}`}>{Math.round(stats.battery)}%</span></span>
                                <span className="text-[10px] sm:text-xs opacity-70 uppercase tracking-wider truncate">{stats.battery_plugged ? 'AC Connected' : 'Discharging'}</span>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
