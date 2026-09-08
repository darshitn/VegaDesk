import { useState, useEffect, useRef } from 'react'
import { Calendar, CheckCircle2, Circle, ListTodo, Plus, Trash2, PenTool } from 'lucide-react'

export default function ProductivityHub() {
  const [tasks, setTasks] = useState([])
  const [newTaskText, setNewTaskText] = useState('')
  const [noteText, setNoteText] = useState('')
  const [isSavingNote, setIsSavingNote] = useState(false)
  const saveTimeoutRef = useRef(null)

  const fetchTasks = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/tasks')
      const data = await res.json()
      setTasks(data)
    } catch (err) {
      console.error('Failed to fetch tasks', err)
    }
  }

  const fetchNotes = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/notes')
      const data = await res.json()
      if (data.text) setNoteText(data.text)
    } catch (err) {
      console.error('Failed to fetch notes', err)
    }
  }

  useEffect(() => {
    fetchTasks()
    fetchNotes()
  }, [])

  const handleAddTask = async (e) => {
    e.preventDefault()
    if (!newTaskText.trim()) return
    try {
      const res = await fetch('http://localhost:8000/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: newTaskText.trim() })
      })
      const newTask = await res.json()
      setTasks([...tasks, newTask])
      setNewTaskText('')
    } catch (err) {
      console.error('Failed to add task', err)
    }
  }

  const toggleTask = async (id) => {
    // Optimistic update
    setTasks(tasks.map(t => t.id === id ? { ...t, completed: !t.completed } : t))
    try {
      await fetch(`http://localhost:8000/api/tasks/${id}`, { method: 'PUT' })
    } catch (err) {
      console.error('Failed to toggle task', err)
      fetchTasks() // revert on error
    }
  }

  const deleteTask = async (id) => {
    setTasks(tasks.filter(t => t.id !== id))
    try {
      await fetch(`http://localhost:8000/api/tasks/${id}`, { method: 'DELETE' })
    } catch (err) {
      console.error('Failed to delete task', err)
      fetchTasks()
    }
  }

  const handleNoteChange = (e) => {
    const text = e.target.value
    setNoteText(text)

    // Debounce save
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    setIsSavingNote(true)
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        const res = await fetch('http://localhost:8000/api/notes', {
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

  // Cleanup pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    }
  }, [])

  const openTasksCount = tasks.filter(t => !t.completed).length

  return (
    <div className="flex flex-col h-full w-full max-w-3xl mx-auto rounded-lg border border-current/20 bg-black/10 backdrop-blur-sm overflow-hidden shadow-inner mt-6">
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
          <span className="text-[10px] sm:text-xs uppercase tracking-wider truncate">Next Event: Connect Google Calendar</span>
        </div>
      </div>

      {/* Main Two Columns */}
      <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-4 p-4 overflow-hidden">
        
        {/* Tasks Column */}
        <div className="flex flex-col h-[300px] md:h-auto bg-black/20 rounded-lg border border-current/10 p-3 overflow-hidden">
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

          <div className="flex-1 overflow-y-auto space-y-2 custom-scrollbar pr-1">
            {tasks.length === 0 && (
              <div className="opacity-30 text-xs italic text-center mt-4">No tasks found.</div>
            )}
            {tasks.map(task => (
              <div key={task.id} className="group flex items-center gap-2 p-2 bg-black/30 border border-current/10 hover:border-[var(--accent)] transition-colors">
                <button onClick={() => toggleTask(task.id)} className="text-[var(--accent)] shrink-0">
                  {task.completed ? <CheckCircle2 size={16} /> : <Circle size={16} />}
                </button>
                <span className={`flex-1 text-sm ${task.completed ? 'line-through opacity-40' : ''} break-words`}>
                  {task.text}
                </span>
                <button onClick={() => deleteTask(task.id)} className="opacity-0 group-hover:opacity-100 text-red-500 hover:text-red-400 transition-opacity shrink-0">
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Scratchpad Column */}
        <div className="flex flex-col h-[300px] md:h-auto bg-black/20 rounded-lg border border-current/10 p-3 relative overflow-hidden">
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
