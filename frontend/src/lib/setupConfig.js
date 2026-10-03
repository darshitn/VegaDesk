/**
 * Default setup configuration and storage persistence helper for onboarding.
 */
export const DEFAULT_SETUP_CONFIG = {
  userName: 'Sir',
  city: 'London',
  theme: 'sci-fi-hud',
  mode: 'Hotkey Overlay',
  llm: 'none', // Default to offline deterministic-only (₹0, no keys or downloads required)
}

export function persistSetupChoices(storage, formData) {
  try {
    storage.setItem('jarvisSetupComplete', 'true')
    storage.setItem('jarvisUserName', (formData.userName || '').trim() || 'Sir')
    storage.setItem('jarvisWeatherCity', (formData.city || '').trim() || 'London')
    if (formData.llm === 'backend') {
      storage.removeItem('jarvisLlmProvider')
    } else {
      storage.setItem('jarvisLlmProvider', formData.llm)
    }
  } catch { /* privacy mode / storage quota */ }
}
