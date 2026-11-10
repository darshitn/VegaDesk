/** Update a wake-word preference only after the server confirms its state. */
export async function confirmVoicePreference(request, enabled) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 8000)
  try {
    const response = await request(enabled, controller.signal)
    if (!response.ok) throw new Error(`Voice setting was not saved (HTTP ${response.status}).`)
    const data = await response.json()
    if (data.error) throw new Error(data.error)
    if (typeof data.enabled !== 'boolean') throw new Error('Backend did not confirm the voice setting.')
    return data.enabled
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('The voice setting request timed out. Check its status before retrying.')
    throw error
  } finally {
    clearTimeout(timer)
  }
}
