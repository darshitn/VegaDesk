// Refresh mounted Hub views from executed actions, never from model prose.
const actions = new Set([
  'create_task', 'set_task_completed', 'start_timer', 'cancel_timer',
  'create_reminder', 'snooze_reminder', 'start_focus_session', 'end_focus_session',
  'register_workspace', 'update_workspace', 'link_task_to_workspace',
  'add_session_note', 'add_coursework', 'complete_coursework',
])
const listeners = new Set()

export function subscribeProductivityUpdates(refresh) {
  listeners.add(refresh)
  return () => listeners.delete(refresh)
}

export function publishProductivityUpdate(response) {
  const receipt = response?.receipt
  if (response?.error || receipt?.success !== true || receipt.read_only ||
      receipt.clarification || !actions.has(receipt.action)) return false
  for (const refresh of listeners) {
    // A view failure must not turn a committed action into a chat error.
    try { refresh() } catch { /* other mounted views still refresh */ }
  }
  return true
}
