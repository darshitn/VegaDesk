import { useState, useEffect, useRef, useCallback } from 'react'
import { Calendar, CheckCircle2, Circle, ListTodo, Plus, Trash2, PenTool, Timer as TimerIcon, BellRing, Target, Play, Square, X, FolderOpen, Rocket, NotebookPen, FolderPlus, GraduationCap } from 'lucide-react'

const API = 'http://localhost:8000'

// Idempotency key for a single user action so retries (double-click, network
// replay) never duplicate a write. Falls back if crypto.randomUUID is absent.
function idemKey(prefix) {
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}-${uuid}`.slice(0, 120)
}

// Format an ISO-UTC timestamp for the user's local time zone.
function fmtLocal(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// Human countdown between now (ms) and an ISO end time.
function fmtCountdown(endIso, nowMs) {
  if (!endIso) return ''
  const diff = new Date(endIso).getTime() - nowMs
  const abs = Math.abs(diff)
  const h = Math.floor(abs / 3600000)
  const m = Math.floor((abs % 3600000) / 60000)
  const s = Math.floor((abs % 60000) / 1000)
  const core = h > 0 ? `${h}h ${m}m` : `${m}m ${String(s).padStart(2, '0')}s`
  return diff >= 0 ? core : `${core} over`
}

export default function ProductivityHub() {
  const [tasks, setTasks] = useState([])
  const [newTaskText, setNewTaskText] = useState('')
  const [noteText, setNoteText] = useState('')
  const [isSavingNote, setIsSavingNote] = useState(false)
  const saveTimeoutRef = useRef(null)

  // M1: aggregated hub state (timers / reminders / focus) + a 1s tick for countdowns
  const [hub, setHub] = useState({ open_tasks: [], active_timers: [], next_reminder: null, active_focus: null, timezone: '' })
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [banner, setBanner] = useState(null) // { kind: 'error'|'info', text }

  // Quick-add forms
  const [timerMinutes, setTimerMinutes] = useState('25')
  const [reminderText, setReminderText] = useState('')
  const [reminderWhen, setReminderWhen] = useState('') // datetime-local value
  const [focusMinutes, setFocusMinutes] = useState('45')
  const [focusObjective, setFocusObjective] = useState('')

  // P2: durable workspaces + "Resume my work" + session closure
  const [projects, setProjects] = useState([])
  const [today, setToday] = useState({ next_actions: [], due_soon: [], active_focus: null })
  const [resumed, setResumed] = useState(null) // {workspace, session_notes, open_tasks, message}
  const [newProject, setNewProject] = useState({ name: '', type: 'personal', next_action: '' })
  const [draft, setDraft] = useState(null) // {outcome, next_action, blocker, idempotency_key}

  // ── P3: academic loop (subject / coursework / due date / effort) ──
  const [coursework, setCoursework] = useState([])
  const [newCw, setNewCw] = useState({ title: '', kind: 'assignment', workspace_id: '', due: '', effort_text: '' })
  const [studyMinutes, setStudyMinutes] = useState('25')
  const [study, setStudy] = useState(null) // {window_minutes, suggestion, alternatives}

  const flash = useCallback((kind, text) => {
    setBanner({ kind, text })
    setTimeout(() => setBanner(b => (b && b.text === text ? null : b)), 5000)
  }, [])

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/tasks`)
      setTasks(await res.json())
    } catch (err) {
      console.error('Failed to fetch tasks', err)
    }
  }, [])

  const fetchNotes = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/notes`)
      const data = await res.json()
      if (data.text) setNoteText(data.text)
    } catch (err) {
      console.error('Failed to fetch notes', err)
    }
  }, [])

  const fetchHub = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/hub/state`)
      if (res.ok) setHub(await res.json())
    } catch {
      /* backend offline — keep last known state */
    }
  }, [])

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/workspaces`)
      if (res.ok) setProjects(await res.json())
    } catch { /* backend offline */ }
  }, [])

  const fetchToday = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/today`)
      if (res.ok) {
        const data = await res.json()
        setToday(data.items || { next_actions: [], due_soon: [], active_focus: null })
      }
    } catch { /* backend offline */ }
  }, [])

  const fetchCoursework = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/coursework`)
      if (res.ok) setCoursework(await res.json())
    } catch { /* backend offline */ }
  }, [])

  const refresh = useCallback(() => {
    fetchTasks()
    fetchHub()
    fetchProjects()
    fetchToday()
    fetchCoursework()
  }, [fetchTasks, fetchHub, fetchProjects, fetchToday, fetchCoursework])

  useEffect(() => {
    fetchTasks()
    fetchNotes()
    fetchHub()
    fetchProjects()
    fetchToday()
    fetchCoursework()
    const hubTimer = setInterval(() => { fetchHub(); fetchToday() }, 5000)
    const tick = setInterval(() => setNowMs(Date.now()), 1000)
    return () => { clearInterval(hubTimer); clearInterval(tick) }
  }, [fetchTasks, fetchNotes, fetchHub, fetchProjects, fetchToday, fetchCoursework])

  // ── Tasks ──────────────────────────────────────────────
  const handleAddTask = async (e) => {
    e.preventDefault()
    if (!newTaskText.trim()) return
    try {
      const res = await fetch(`${API}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: newTaskText.trim() })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setNewTaskText('')
      refresh()
    } catch (err) {
      console.error('Failed to add task', err)
      flash('error', 'Could not add task.')
    }
  }

  // Idempotent set-completion (retries can't reverse state like a toggle would).
  const setCompleted = async (id, completed) => {
    setTasks(prev => prev.map(t => t.id === id ? { ...t, completed } : t))
    try {
      const res = await fetch(`${API}/api/tasks/${id}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (err) {
      console.error('Failed to update task', err)
      fetchTasks()
    }
  }

  const deleteTask = async (id) => {
    setTasks(prev => prev.filter(t => t.id !== id))
    try {
      await fetch(`${API}/api/tasks/${id}`, { method: 'DELETE' })
      fetchHub()
    } catch (err) {
      console.error('Failed to delete task', err)
      fetchTasks()
    }
  }

  // ── Timers ─────────────────────────────────────────────
  const addTimer = async (e) => {
    e.preventDefault()
    const mins = parseInt(timerMinutes, 10)
    if (!mins || mins < 1) { flash('error', 'Enter a timer length in minutes.'); return }
    try {
      const res = await fetch(`${API}/api/timers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration_seconds: mins * 60, label: `${mins}m timer` })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTimerMinutes('25')
      refresh()
    } catch (err) {
      console.error('Failed to start timer', err)
      flash('error', 'Could not start timer.')
    }
  }

  const cancelTimer = async (id) => {
    try {
      await fetch(`${API}/api/timers/${id}`, { method: 'DELETE' })
      refresh()
    } catch (err) {
      console.error('Failed to cancel timer', err)
      flash('error', 'Could not cancel timer.')
    }
  }

  // ── Reminders ──────────────────────────────────────────
  const addReminder = async (e) => {
    e.preventDefault()
    if (!reminderText.trim() || !reminderWhen) {
      flash('error', 'Reminder needs both a note and a date/time.')
      return
    }
    const dueIso = new Date(reminderWhen).toISOString()
    try {
      const res = await fetch(`${API}/api/reminders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: reminderText.trim(), due_utc: dueIso })
      })
      if (res.status === 409) {
        const detail = await res.json().catch(() => ({}))
        flash('error', detail.detail || 'That time is in the past — pick a future time.')
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setReminderText('')
      setReminderWhen('')
      refresh()
    } catch (err) {
      console.error('Failed to create reminder', err)
      flash('error', 'Could not create reminder.')
    }
  }

  const snoozeReminder = async (id) => {
    try {
      const res = await fetch(`${API}/api/reminders/${id}/snooze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes: 10 })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      flash('info', 'Reminder snoozed for 10 minutes.')
      refresh()
    } catch (err) {
      console.error('Failed to snooze reminder', err)
      flash('error', 'Could not snooze reminder.')
    }
  }

  // ── Focus ──────────────────────────────────────────────
  const startFocus = async (e) => {
    e.preventDefault()
    const mins = parseInt(focusMinutes, 10)
    if (!mins || mins < 1) { flash('error', 'Enter a focus length in minutes.'); return }
    try {
      const res = await fetch(`${API}/api/focus/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration_seconds: mins * 60, objective: focusObjective.trim() })
      })
      if (res.status === 400) {
        const detail = await res.json().catch(() => ({}))
        flash('error', detail.detail || 'A focus session is already running.')
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setFocusObjective('')
      refresh()
    } catch (err) {
      console.error('Failed to start focus', err)
      flash('error', 'Could not start focus session.')
    }
  }

  const endFocus = async () => {
    try {
      const res = await fetch(`${API}/api/focus/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: '' })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      flash('info', 'Focus session ended.')
      refresh()
    } catch (err) {
      console.error('Failed to end focus', err)
      flash('error', 'Could not end focus session.')
    }
  }

  // ── P2: projects / resume / session closure ────────────
  const registerProject = async (e) => {
    e.preventDefault()
    if (!newProject.name.trim()) { flash('error', 'Give the project a name.'); return }
    try {
      const res = await fetch(`${API}/api/workspaces`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newProject.name.trim(), type: newProject.type,
          next_action: newProject.next_action.trim() || undefined,
          idempotency_key: idemKey('reg'),
        })
      })
      if (res.status === 409 || res.status === 400) {
        const detail = await res.json().catch(() => ({}))
        flash('error', detail.detail || 'Could not register project.')
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setNewProject({ name: '', type: 'personal', next_action: '' })
      flash('info', 'Project registered.')
      refresh()
    } catch (err) {
      console.error('Failed to register project', err)
      flash('error', 'Could not register project.')
    }
  }

  const resumeProject = async (id) => {
    try {
      const res = await fetch(`${API}/api/workspaces/${id}/resume`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setResumed(await res.json())
    } catch (err) {
      console.error('Failed to resume project', err)
      flash('error', 'Could not resume that project.')
    }
  }

  const setProjectNextAction = async (id, nextAction) => {
    try {
      const res = await fetch(`${API}/api/workspaces/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ next_action: nextAction, idempotency_key: idemKey('next') })
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      refresh()
    } catch (err) {
      console.error('Failed to update next action', err)
      flash('error', 'Could not update the next action.')
    }
  }

  const openEndSession = async () => {
    try {
      const res = await fetch(`${API}/api/session/draft`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const d = data.draft || { outcome: '', next_action: '', blocker: '' }
      setDraft({ ...d, idempotency_key: idemKey('session') })
    } catch (err) {
      console.error('Failed to build session draft', err)
      flash('error', 'Could not build today\'s summary.')
    }
  }

  const saveSessionNote = async () => {
    if (!draft) return
    try {
      const res = await fetch(`${API}/api/session/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          outcome: draft.outcome || undefined,
          blocker: draft.blocker || undefined,
          next_action: draft.next_action || undefined,
          // Attach to the project the user just resumed (their own action, not a
          // model guess). Omitted when nothing was resumed → a day-level note.
          workspace_id: resumed?.workspace?.id || undefined,
          idempotency_key: draft.idempotency_key,
        })
      })
      if (res.status === 409 || res.status === 400) {
        const detail = await res.json().catch(() => ({}))
        flash('error', detail.detail || 'Nothing to save yet.')
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDraft(null)
      flash('info', 'Session note saved.')
      refresh()
    } catch (err) {
      console.error('Failed to save session note', err)
      flash('error', 'Could not save the session note.')
    }
  }

  // ── P3: coursework + "I have N minutes" suggestion ─────
  const addCoursework = async (e) => {
    e.preventDefault()
    if (!newCw.title.trim()) { flash('error', 'Give the assignment or exam a name.'); return }
    try {
      const res = await fetch(`${API}/api/coursework`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: newCw.title.trim(),
          kind: newCw.kind,
          workspace_id: newCw.workspace_id ? Number(newCw.workspace_id) : undefined,
          due: newCw.due.trim() || undefined,          // phrase; backend validates tz + past dates
          effort_text: newCw.effort_text.trim() || undefined,
          idempotency_key: idemKey('cw'),
        })
      })
      if (res.status === 400 || res.status === 409) {
        const detail = await res.json().catch(() => ({}))
        flash('error', detail.detail || 'Could not log that item.')
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setNewCw({ title: '', kind: 'assignment', workspace_id: newCw.workspace_id, due: '', effort_text: '' })
      flash('info', data.receipt?.message || 'Coursework logged.')
      refresh()
    } catch (err) {
      console.error('Failed to add coursework', err)
      flash('error', 'Could not log that item.')
    }
  }

  const completeCoursework = async (id) => {
    try {
      const res = await fetch(`${API}/api/coursework/${id}/complete`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      // Don't leave a stale "Do this: …" card pointing at finished work.
      if (study?.suggestion?.id === id) setStudy(null)
      flash('info', 'Marked done.')
      refresh()
    } catch (err) {
      console.error('Failed to complete coursework', err)
      flash('error', 'Could not mark that item done.')
    }
  }

  const askStudy = async (minutes) => {
    const mins = Math.max(5, Math.min(1440, Number(minutes) || 25))
    try {
      const res = await fetch(`${API}/api/study/suggest?minutes=${mins}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setStudy(data.items || null)
      setStudyMinutes(String(mins))
    } catch (err) {
      console.error('Failed to get study suggestion', err)
      flash('error', 'Could not build a suggestion.')
    }
  }

  // Editable choice: the user overrides VEGA's pick with an alternative. The
  // displaced pick goes back into the list so the choice stays two-way.
  const pickStudy = (item) => {
    if (!study || !study.suggestion) return
    const prev = study.suggestion
    setStudy({
      ...study,
      suggestion: item,
      alternatives: [prev, ...study.alternatives.filter(a => a.id !== item.id)],
    })
  }

  // Hand the chosen item to the existing focus-session form (no new timer type).
  const sendToFocus = (item) => {
    setFocusObjective(`${item.kind}: ${item.title}`)
    const fit = Math.min(item.effort_minutes || study?.window_minutes || 25, study?.window_minutes || 25)
    setFocusMinutes(String(fit))
    flash('info', 'Loaded into Focus — start it when you are ready.')
  }

  // ── Notes (debounced autosave — unchanged behavior) ────
  const handleNoteChange = (e) => {
    const text = e.target.value
    setNoteText(text)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    setIsSavingNote(true)
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/api/notes`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text })
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
      } catch (err) {
        console.error('Failed to save notes', err)
      } finally {
        setIsSavingNote(false)
      }
    }, 1000)
  }

  useEffect(() => {
    return () => { if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current) }
  }, [])

  const openTasksCount = tasks.filter(t => !t.completed).length
  const activeTimers = hub.active_timers || []
  const nextReminder = hub.next_reminder
  const activeFocus = hub.active_focus

  return (
    <div className="flex flex-col w-full max-w-3xl mx-auto rounded-lg border border-current/20 bg-black/10 backdrop-blur-sm overflow-hidden shadow-inner mt-6">
      {/* Header / Strip */}
      <div className="flex items-center justify-between p-3 border-b border-current/20 bg-black/20">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <ListTodo size={16} className="text-[var(--accent)]" />
            <span className="text-sm font-semibold tracking-wider">PRODUCTIVITY HUB</span>
          </div>
          <div className="hidden sm:block h-4 w-px bg-current opacity-20"></div>
          <span className="hidden sm:block text-xs font-bold text-[var(--accent)]">{openTasksCount} Open Tasks</span>
        </div>
        <div className="flex items-center gap-2 opacity-60">
          <Calendar size={14} />
          <span className="text-[10px] sm:text-xs uppercase tracking-wider truncate">{hub.timezone || 'Local time'}</span>
        </div>
      </div>

      {/* Inline status banner */}
      {banner && (
        <div className={`px-3 py-1.5 text-xs border-b ${banner.kind === 'error'
          ? 'bg-red-500/15 border-red-500/30 text-red-300'
          : 'bg-[var(--accent)]/10 border-[var(--accent)]/20 text-[var(--accent)]'}`}>
          {banner.text}
        </div>
      )}

      {/* Alerts that fired while the overlay was hidden: a socket accepted them
          but nobody could see them, so they are re-listed here (last 6 h). */}
      {hub.recent_alerts?.length || hub.missed_alerts?.length ? (
        <div className="px-3 py-2 border-b border-current/20 bg-black/20">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2 mb-1">
            <BellRing size={12} /> Alerts in the last 6 hours
          </h4>
          <ul className="flex flex-col gap-0.5 max-h-28 overflow-y-auto">
            {(hub.recent_alerts || []).map(a => (
              <li key={`ra-${a.id}`} className="text-xs flex items-baseline gap-2 min-w-0">
                <span className="opacity-50 tabular-nums shrink-0">{fmtLocal(a.delivered_at || a.due_utc)}</span>
                <span className="truncate">{a.message}</span>
              </li>
            ))}
            {(hub.missed_alerts || []).map(a => (
              <li key={`ma-${a.id}`} className="text-xs flex items-baseline gap-2 min-w-0 text-amber-300">
                <span className="opacity-50 tabular-nums shrink-0">{fmtLocal(a.due_utc)}</span>
                <span className="truncate">Missed while away: {a.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Active-now cards: timers, next reminder, focus */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 border-b border-current/20">
        {/* Timers */}
        <div className="bg-black/20 rounded-lg border border-current/10 p-3 flex flex-col gap-2">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2">
            <TimerIcon size={12} /> Timers
          </h4>
          {activeTimers.length === 0 && <p className="text-xs opacity-40 italic">No active timers.</p>}
          {activeTimers.map(t => (
            <div key={t.id} className="flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0">
                <div className="truncate font-semibold">{t.label || 'Timer'}</div>
                <div className="text-[var(--accent)] tabular-nums">{fmtCountdown(t.end_utc, nowMs)}</div>
              </div>
              <button onClick={() => cancelTimer(t.id)} className="text-red-400 hover:text-red-300 shrink-0" title="Cancel timer">
                <X size={14} />
              </button>
            </div>
          ))}
          <form onSubmit={addTimer} className="flex gap-1 mt-auto">
            <input
              type="number" min="1" max="1440" value={timerMinutes}
              onChange={(e) => setTimerMinutes(e.target.value)}
              className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
              placeholder="min"
            />
            <button type="submit" className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 hover:bg-[var(--accent)]/40 border border-[var(--accent)]/30 shrink-0" title="Start timer">
              <Plus size={14} />
            </button>
          </form>
        </div>

        {/* Next reminder */}
        <div className="bg-black/20 rounded-lg border border-current/10 p-3 flex flex-col gap-2">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2">
            <BellRing size={12} /> Next Reminder
          </h4>
          {nextReminder ? (
            <div className="text-xs">
              <div className="truncate font-semibold">{nextReminder.text}</div>
              <div className="opacity-70">{fmtLocal(nextReminder.due_utc)}</div>
              <div className="text-[var(--accent)] tabular-nums">{fmtCountdown(nextReminder.due_utc, nowMs)}</div>
              <button onClick={() => snoozeReminder(nextReminder.id)} className="mt-1 text-[10px] underline opacity-80 hover:opacity-100">
                Snooze 10 min
              </button>
            </div>
          ) : (
            <p className="text-xs opacity-40 italic">Nothing scheduled.</p>
          )}
          <form onSubmit={addReminder} className="flex flex-col gap-1 mt-auto">
            <input
              type="text" value={reminderText}
              onChange={(e) => setReminderText(e.target.value)}
              className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
              placeholder="Remind me to..."
            />
            <div className="flex gap-1">
              <input
                type="datetime-local" value={reminderWhen}
                onChange={(e) => setReminderWhen(e.target.value)}
                className="w-full bg-black/30 border border-current/20 text-[10px] p-1 outline-none font-inherit"
              />
              <button type="submit" className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 hover:bg-[var(--accent)]/40 border border-[var(--accent)]/30 shrink-0" title="Add reminder">
                <Plus size={14} />
              </button>
            </div>
          </form>
        </div>

        {/* Focus */}
        <div className="bg-black/20 rounded-lg border border-current/10 p-3 flex flex-col gap-2">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2">
            <Target size={12} /> Focus
          </h4>
          {activeFocus ? (
            <div className="text-xs">
              <div className="truncate font-semibold">{activeFocus.objective || 'Session'}</div>
              <div className="text-[var(--accent)] tabular-nums">{fmtCountdown(activeFocus.planned_end_utc, nowMs)}</div>
              <button onClick={endFocus} className="mt-1 flex items-center gap-1 text-[10px] text-red-400 hover:text-red-300">
                <Square size={10} /> End session
              </button>
            </div>
          ) : (
            <p className="text-xs opacity-40 italic">No active session.</p>
          )}
          {!activeFocus && (
            <form onSubmit={startFocus} className="flex flex-col gap-1 mt-auto">
              <input
                type="text" value={focusObjective}
                onChange={(e) => setFocusObjective(e.target.value)}
                className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
                placeholder="Objective (e.g. DSA)"
              />
              <div className="flex gap-1">
                <input
                  type="number" min="1" max="480" value={focusMinutes}
                  onChange={(e) => setFocusMinutes(e.target.value)}
                  className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
                  placeholder="min"
                />
                <button type="submit" className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 hover:bg-[var(--accent)]/40 border border-[var(--accent)]/30 shrink-0" title="Start focus session">
                  <Play size={14} />
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      {/* TODAY & PROJECTS — durable "Resume my work" + session closure */}
      <div className="border-b border-current/20 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2">
            <Rocket size={12} className="text-[var(--accent)]" /> Today &amp; Projects
          </h4>
          <button
            onClick={openEndSession}
            className="flex items-center gap-1 text-[11px] bg-[var(--accent)]/15 text-[var(--accent)] px-2 py-1 rounded border border-[var(--accent)]/30 hover:bg-[var(--accent)]/30 transition-colors"
            title="Draft a session summary from today's real activity">
            <NotebookPen size={12} /> End session
          </button>
        </div>

        {/* Today strip: next actions + due soon */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs mb-2">
          <div className="bg-black/20 rounded border border-current/10 p-2">
            <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Next actions</div>
            {(today.next_actions || []).length === 0 && (
              <div className="opacity-40 italic">No project next actions.</div>
            )}
            {(today.next_actions || []).slice(0, 4).map(n => (
              <div key={n.workspace_id} className="flex items-center justify-between gap-2 py-0.5">
                <span className="truncate">{n.name}: <span className="opacity-80">{n.next_action}</span></span>
                <button onClick={() => resumeProject(n.workspace_id)}
                  className="shrink-0 text-[10px] underline opacity-80 hover:opacity-100">Resume</button>
              </div>
            ))}
          </div>
          <div className="bg-black/20 rounded border border-current/10 p-2">
            <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Due soon</div>
            {(today.due_soon || []).length === 0 && (today.coursework || []).length === 0 && (
              <div className="opacity-40 italic">Nothing due in the next 2 days.</div>
            )}
            {(today.due_soon || []).slice(0, 4).map(t => (
              <div key={t.task_id} className="truncate py-0.5 flex items-center gap-1">
                <Calendar size={10} className="opacity-60 shrink-0" />
                <span>{t.text}</span><span className="opacity-50">· {fmtLocal(t.deadline_utc)}</span>
              </div>
            ))}
            {(today.coursework || []).filter(c => c.due_in_days <= 2).slice(0, 4).map(c => (
              <div key={`cw-${c.id}`} className="truncate py-0.5 flex items-center gap-1">
                <GraduationCap size={10} className="text-[var(--accent)] shrink-0" />
                <span title={c.why}>{c.kind}: {c.title}</span>
                <span className="opacity-50">· {c.why}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Project register + list */}
        <form onSubmit={registerProject} className="flex flex-wrap gap-1 mb-2">
          <input
            type="text" value={newProject.name}
            onChange={(e) => setNewProject(p => ({ ...p, name: e.target.value }))}
            className="flex-1 min-w-[120px] bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
            placeholder="Register a project (name)" aria-label="Project name"
          />
          <select
            value={newProject.type}
            onChange={(e) => setNewProject(p => ({ ...p, type: e.target.value }))}
            className="bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit" aria-label="Project type">
            <option value="personal">Personal</option>
            <option value="academic">Academic</option>
          </select>
          <input
            type="text" value={newProject.next_action}
            onChange={(e) => setNewProject(p => ({ ...p, next_action: e.target.value }))}
            className="flex-1 min-w-[120px] bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
            placeholder="First next action (optional)" aria-label="First next action"
          />
          <button type="submit" title="Register project" aria-label="Register project"
            className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 hover:bg-[var(--accent)]/40 border border-[var(--accent)]/30 shrink-0">
            <FolderPlus size={14} />
          </button>
        </form>

        {projects.length === 0 ? (
          <div className="opacity-40 text-xs italic">No projects yet. Register one above or say "register a project called …".</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {projects.map(p => (
              <div key={p.id} className="flex items-center gap-2 bg-black/25 border border-current/10 rounded px-2 py-1 text-xs">
                <FolderOpen size={12} className="text-[var(--accent)] shrink-0" />
                <span className="truncate max-w-[160px]" title={p.name}>{p.name}</span>
                <span className="opacity-50 text-[10px]">{p.type}</span>
                <button onClick={() => resumeProject(p.id)}
                  className="ml-1 text-[10px] bg-[var(--accent)]/15 text-[var(--accent)] px-1.5 py-0.5 rounded hover:bg-[var(--accent)]/30">Resume</button>
              </div>
            ))}
          </div>
        )}

        {/* Resumed project detail (read-only view) */}
        {resumed && resumed.workspace && (
          <div className="mt-2 bg-black/30 border border-[var(--accent)]/30 rounded p-2 text-xs space-y-1">
            <div className="flex items-center justify-between">
              <div className="font-semibold text-[var(--accent)]">Resuming: {resumed.workspace.name} ({resumed.workspace.type})</div>
              <button onClick={() => setResumed(null)} className="opacity-60 hover:opacity-100" title="Close" aria-label="Close resume view"><X size={12} /></button>
            </div>
            {resumed.workspace.goal && <div><span className="opacity-60">Goal:</span> {resumed.workspace.goal}</div>}
            <div>
              <span className="opacity-60">Next action:</span>{' '}
              <input
                defaultValue={resumed.workspace.next_action || ''}
                onBlur={(e) => e.target.value !== (resumed.workspace.next_action || '') &&
                  setProjectNextAction(resumed.workspace.id, e.target.value)}
                className="bg-black/30 border border-current/20 text-xs px-1 py-0.5 outline-none font-inherit w-[70%]"
                aria-label="Edit next action"
              />
            </div>
            {resumed.workspace.blocker && <div><span className="opacity-60">Blocker:</span> {resumed.workspace.blocker}</div>}
            {(resumed.session_notes || [])[0] && (
              <div className="opacity-80">Last note ({fmtLocal(resumed.session_notes[0].created_utc)}): {resumed.session_notes[0].outcome || '—'}</div>
            )}
            {(resumed.open_tasks || []).length > 0 && (
              <div className="opacity-80">Open tasks: {resumed.open_tasks.slice(0, 6).map(t => `#${t.id} ${t.text}`).join(' · ')}</div>
            )}
            {resumed.workspace.path && <div className="opacity-60 break-all">Folder: {resumed.workspace.path}</div>}
          </div>
        )}

        {/* End-session draft (edit before saving; saved once via idempotency key) */}
        {draft && (
          <div className="mt-2 bg-black/30 border border-[var(--accent)]/30 rounded p-2 text-xs space-y-2">
            <div className="font-semibold text-[var(--accent)]">Wrap up today — review &amp; edit before saving</div>
            <textarea
              value={draft.outcome}
              onChange={(e) => setDraft(d => ({ ...d, outcome: e.target.value }))}
              className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit min-h-[48px]"
              placeholder="Outcome (what you actually got done)" aria-label="Session outcome"
            />
            <input
              value={draft.next_action}
              onChange={(e) => setDraft(d => ({ ...d, next_action: e.target.value }))}
              className="w-full bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
              placeholder="Next action for next time" aria-label="Session next action"
            />
            <div className="flex gap-2">
              <button onClick={saveSessionNote}
                className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 py-1 rounded border border-[var(--accent)]/30 hover:bg-[var(--accent)]/40">
                Save session note
              </button>
              <button onClick={() => setDraft(null)} className="px-2 py-1 rounded border border-current/20 hover:opacity-80">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* STUDY PLAN — Stage 3 academic loop: subject, item, due date, own effort
          estimate, and an "I have N minutes" suggestion the user can override. */}
      <div className="border-b border-current/20 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h4 className="text-[10px] uppercase tracking-widest opacity-70 flex items-center gap-2">
            <GraduationCap size={12} className="text-[var(--accent)]" /> Study plan
          </h4>
          <form className="flex items-center gap-1 text-xs"
            onSubmit={(e) => { e.preventDefault(); askStudy(studyMinutes) }}>
            <span className="opacity-60">I have</span>
            <input
              type="number" min="5" max="1440" value={studyMinutes}
              onChange={(e) => setStudyMinutes(e.target.value)}
              className="w-16 bg-black/30 border border-current/20 text-xs p-1 outline-none font-inherit"
              aria-label="Available minutes" title="Minutes available right now"
            />
            <span className="opacity-60">min</span>
            <button type="submit"
              className="bg-[var(--accent)]/15 text-[var(--accent)] px-2 py-1 rounded border border-[var(--accent)]/30 hover:bg-[var(--accent)]/30">
              Suggest
            </button>
          </form>
        </div>

        {study && (
          <div className="bg-black/30 border border-[var(--accent)]/30 rounded p-2 text-xs mb-2 space-y-1">
            {!study.suggestion ? (
              <div className="opacity-60 italic">{study.window_minutes} min free, but nothing open to suggest. Log an item below.</div>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <div className="font-semibold text-[var(--accent)]">
                    Do this: {study.suggestion.kind} &ldquo;{study.suggestion.title}&rdquo;
                  </div>
                  <button onClick={() => setStudy(null)} className="opacity-60 hover:opacity-100" title="Dismiss" aria-label="Dismiss suggestion"><X size={12} /></button>
                </div>
                <div className="opacity-80">{study.suggestion.why_due} · {study.suggestion.fit}</div>
                <div className="flex gap-2">
                  <button onClick={() => sendToFocus(study.suggestion)}
                    className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 py-0.5 rounded border border-[var(--accent)]/30 hover:bg-[var(--accent)]/40">
                    Use in Focus
                  </button>
                  <button onClick={() => completeCoursework(study.suggestion.id)}
                    className="px-2 py-0.5 rounded border border-current/20 hover:opacity-80">
                    It&rsquo;s already done
                  </button>
                </div>
              </>
            )}
            {[...(study.alternatives || [])].filter(a => a.id !== study.suggestion?.id).length > 0 && (
              <div className="pt-1 border-t border-current/10">
                <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Rather this? (your choice)</div>
                {[...(study.alternatives || [])].filter(a => a.id !== study.suggestion?.id).map(a => (
                  <div key={a.id} className="flex items-center justify-between gap-2 py-0.5">
                    <span className="truncate">#{a.id} {a.kind} {a.title} <span className="opacity-50">· {a.why_due} · {a.fit}</span></span>
                    <button onClick={() => pickStudy(a)}
                      className="shrink-0 text-[10px] underline opacity-80 hover:opacity-100">Pick</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <form onSubmit={addCoursework} className="flex flex-wrap gap-1 mb-2">
          <input
            type="text" value={newCw.title}
            onChange={(e) => setNewCw(c => ({ ...c, title: e.target.value }))}
            className="flex-1 min-w-[140px] bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
            placeholder="Assignment / exam title" aria-label="Coursework title"
          />
          <select value={newCw.kind}
            onChange={(e) => setNewCw(c => ({ ...c, kind: e.target.value }))}
            className="bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit" aria-label="Coursework kind">
            <option value="assignment">Assignment</option>
            <option value="exam">Exam</option>
            <option value="lab">Lab</option>
            <option value="reading">Reading</option>
            <option value="project">Project</option>
          </select>
          <select value={newCw.workspace_id}
            onChange={(e) => setNewCw(c => ({ ...c, workspace_id: e.target.value }))}
            className="bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit" aria-label="Subject">
            <option value="">No subject</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <input
            type="text" value={newCw.due}
            onChange={(e) => setNewCw(c => ({ ...c, due: e.target.value }))}
            className="flex-1 min-w-[110px] bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
            placeholder="Due (e.g. friday, 25 oct, next monday)" aria-label="Due date"
          />
          <input
            type="text" value={newCw.effort_text}
            onChange={(e) => setNewCw(c => ({ ...c, effort_text: e.target.value }))}
            className="w-[130px] bg-black/30 border border-current/20 text-xs p-1.5 outline-none font-inherit"
            placeholder="Effort (e.g. 90 min)" aria-label="Estimated effort"
          />
          <button type="submit" title="Log coursework" aria-label="Log coursework"
            className="bg-[var(--accent)]/20 text-[var(--accent)] px-2 hover:bg-[var(--accent)]/40 border border-[var(--accent)]/30 shrink-0">
            <Plus size={14} />
          </button>
        </form>

        {coursework.length === 0 ? (
          <div className="opacity-40 text-xs italic">Nothing open. Log an assignment, or say &ldquo;add assignment &lt;title&gt; due friday about 90 minutes&rdquo;.</div>
        ) : (
          <div className="space-y-1">
            {coursework.slice(0, 8).map(c => (
              <div key={c.id} className="flex items-center gap-2 text-xs bg-black/25 border border-current/10 rounded px-2 py-1">
                <button onClick={() => completeCoursework(c.id)} title="Mark done" aria-label={`Mark ${c.title} done`}
                  className="shrink-0 opacity-70 hover:opacity-100">
                  <Circle size={12} />
                </button>
                <GraduationCap size={11} className="text-[var(--accent)] shrink-0" />
                <span className="truncate">{c.kind}: {c.title}</span>
                <span className="opacity-50 shrink-0">
                  {c.due_utc ? `· due ${fmtLocal(c.due_utc)}` : '· no due date'}
                  {c.effort_minutes ? ` · ~${c.effort_minutes} min` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Main Two Columns — content-sized so rows are never clipped by a
          fixed-height ancestor; the dashboard column scrolls to reveal them. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">

        {/* Tasks Column */}
        <div className="flex flex-col min-h-[280px] bg-black/20 rounded-lg border border-current/10 p-3">
          <h3 className="text-xs uppercase tracking-widest opacity-70 mb-3 flex items-center gap-2 shrink-0">
            <CheckCircle2 size={14} /> Task Matrix
          </h3>

          <form onSubmit={handleAddTask} className="flex gap-2 mb-3 shrink-0">
            <input
              type="text"
              placeholder="New Task..."
              value={newTaskText}
              onChange={(e) => setNewTaskText(e.target.value)}
              className="flex-1 bg-black/30 border border-current/20 text-sm p-2 outline-none font-inherit"
            />
            <button type="submit" className="bg-[var(--accent)]/20 text-[var(--accent)] p-2 hover:bg-[var(--accent)]/40 transition-colors border border-[var(--accent)]/30">
              <Plus size={18} />
            </button>
          </form>

          <div className="flex-1 overflow-y-auto max-h-[360px] min-h-[120px] space-y-2 custom-scrollbar pr-1">
            {tasks.length === 0 && (
              <div className="opacity-30 text-xs italic text-center mt-4">No tasks found.</div>
            )}
            {tasks.map(task => (
              <div key={task.id} className="group flex items-start gap-2 p-2 bg-black/30 border border-current/10 hover:border-[var(--accent)] transition-colors">
                <button onClick={() => setCompleted(task.id, !task.completed)} className="text-[var(--accent)] shrink-0 mt-0.5" title={task.completed ? 'Mark as not done' : 'Mark as done'}>
                  {task.completed ? <CheckCircle2 size={16} /> : <Circle size={16} />}
                </button>
                <div className="flex-1 min-w-0">
                  <span className={`block text-sm break-words ${task.completed ? 'line-through opacity-40' : ''}`}>
                    {task.text}
                  </span>
                  {task.deadline_utc && (
                    <span className="block text-[10px] opacity-60 mt-0.5 flex items-center gap-1">
                      <Calendar size={10} /> Due {fmtLocal(task.deadline_utc)}
                    </span>
                  )}
                </div>
                <button onClick={() => deleteTask(task.id)} aria-label={`Delete task ${task.id}: ${task.text}`} className="opacity-0 group-hover:opacity-100 focus:opacity-100 group-focus-within:opacity-100 text-red-500 hover:text-red-400 focus-visible:text-red-400 transition-opacity shrink-0 mt-0.5">
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Scratchpad Column */}
        <div className="flex flex-col min-h-[280px] bg-black/20 rounded-lg border border-current/10 p-3 relative">
          <h3 className="text-xs uppercase tracking-widest opacity-70 mb-3 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <PenTool size={14} /> Quick Notes
            </div>
            {isSavingNote && <span className="text-[10px] text-[var(--accent)] animate-pulse">Autosaving...</span>}
          </h3>

          <textarea
            value={noteText}
            onChange={handleNoteChange}
            placeholder="Initialize scratchpad..."
            className="flex-1 w-full bg-transparent resize-none outline-none text-sm font-inherit leading-relaxed custom-scrollbar pr-1"
          />
        </div>

      </div>
    </div>
  )
}
