/** Turn a displayed reply into speech without changing the displayed evidence. */
export function prepareSpeechText(text) {
  if (typeof text !== 'string') return ''

  // A relative timer starts now; its duration is enough for spoken confirmation.
  const timerConfirmation = text.match(/^\s*Timer #\d+ set for (\d+ (?:seconds?|minutes?|hours?)(?: \d+ minutes)?) — ends [^\n]+\s*$/i)
  if (timerConfirmation) return `Timer set for ${timerConfirmation[1]}.`

  // Tools include a readable local time followed by a precise UTC timestamp.
  // Omit that duplicate machine timestamp; keep ordinary parenthetical content.
  const isoTimestamp = String.raw`\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})`
  const timestampAnnotation = new RegExp(
    String.raw`(?:\(\s*${isoTimestamp}\s*\)|\[\s*${isoTimestamp}\s*\])`, 'g',
  )

  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\{[\s\S]*?\}/g, ' ')
    .replace(/\[([^\]\n]+)\]\([^\s)]+\)/g, '$1')
    .replace(timestampAnnotation, ' ')
    .replace(/(^|\n)\s*\[(?:SYSTEM ERROR|TRANSCRIPTION|OFFLINE(?:\s*[-·]\s*INSTANT)?)\]\s*/gi, '$1')
    // Sanitize wake phrases so VEGA's synthesized voice never speaks its own wake trigger into the room.
    .replace(/\bhey\s+jarvis\b/gi, 'VEGA')
    // Read meaningful bracketed prose, without saying its punctuation.
    .replace(/[()[\]]/g, ' ')
    .replace(/[*_#`>|]/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\s+([.,!?;:])/g, '$1')
    .trim()
}
