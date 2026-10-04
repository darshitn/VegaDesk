import { test } from 'node:test'
import assert from 'node:assert/strict'
import { prepareSpeechText } from '../src/lib/speechText.js'

test('speech: timer confirmation speaks only duration', () => {
  const displayed = 'Timer #10 set for 15 seconds — ends Sunday, 04 October 2026 at 10:40 AM (2026-10-04T05:10:54.987425Z).'
  assert.equal(prepareSpeechText(displayed),
    'Timer set for 15 seconds.')
  assert.ok(displayed.includes('(2026-10-04T05:10:54.987425Z)'), 'Displayed text stays intact')
})

test('speech: short timer confirmations retain seconds, minutes, and hours', () => {
  for (const duration of ['25 seconds', '1 second', '25 minutes', '1 hour']) {
    assert.equal(prepareSpeechText(`Timer #3 set for ${duration} — ends tomorrow at noon (2026-10-05T06:30:00Z).`),
      `Timer set for ${duration}.`)
  }
  assert.equal(prepareSpeechText('Could not set timer. Permission denied.'),
    'Could not set timer. Permission denied.')
})

test('speech: reminder and focus timestamp annotations support UTC offsets', () => {
  assert.equal(prepareSpeechText('Reminder due at 10 AM [2026-10-04T10:00:00+05:30].'),
    'Reminder due at 10 AM.')
  assert.equal(prepareSpeechText('Focus ends at noon (2026-10-04T12:00Z).'),
    'Focus ends at noon.')
})

test('speech: skips technical labels but preserves errors and refusals', () => {
  assert.equal(prepareSpeechText('[SYSTEM ERROR] Permission denied. Do not retry.'),
    'Permission denied. Do not retry.')
  assert.equal(prepareSpeechText('[OFFLINE - INSTANT] No AI model selected.'),
    'No AI model selected.')
})

test('speech: retains meaningful parenthetical and bracketed explanations', () => {
  assert.equal(prepareSpeechText('Timer cancelled (tea break). Use [local AI] only if enabled.'),
    'Timer cancelled tea break. Use local AI only if enabled.')
})

test('speech: reads markdown link labels and prose while skipping fenced code', () => {
  assert.equal(prepareSpeechText('See [the guide](https://example.com/guide).\n```js\nrun();\n```\n**Try again.**'),
    'See the guide. Try again.')
})

test('speech: bare timestamps remain when they are the only time provided', () => {
  assert.equal(prepareSpeechText('Due: 2026-10-04T05:10:54Z.'), 'Due: 2026-10-04T05:10:54Z.')
})

test('speech: empty, non-text, or technical-only replies do not produce an utterance', () => {
  for (const text of ['', '  ', null, undefined, {}, '```js\nrun();\n```', '(2026-10-04T05:10:54Z)']) {
    assert.equal(prepareSpeechText(text), '')
  }
})

test('speech: sanitizes wake phrase to prevent self-triggering', () => {
  assert.equal(prepareSpeechText('Say Hey Jarvis to wake the assistant.'),
    'Say VEGA to wake the assistant.')
  assert.equal(prepareSpeechText('You can say hey jarvis anytime.'),
    'You can say VEGA anytime.')
})

