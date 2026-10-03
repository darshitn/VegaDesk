/**
 * Deterministic seed runner for VEGA Beta Acceptance Profile.
 *
 * Guarantees:
 * 1. Pre-configures VEGA_PROFILE=beta and isolated beta paths BEFORE Python or DB imports.
 * 2. Strict Python interpreter resolution consistent with the launcher.
 * 3. Defensive validation of the synthetic root before file creation.
 * 4. Transparent stdio forwarding and exit code propagation.
 */

import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  getBetaEnvironment,
  resolvePythonExecutable,
} from './launcherHelpers.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')

const { env, betaRoot, betaDbPath } = getBetaEnvironment()

// Basic defensive sanity check on root path before launch
const normalizedRoot = path.normalize(betaRoot)
if (
  normalizedRoot.includes('..') ||
  normalizedRoot === path.parse(normalizedRoot).root ||
  normalizedRoot.toLowerCase().includes('windows\\system32')
) {
  console.error(
    `[FATAL SEED SAFETY ERROR] Refusing to seed unsafe root path: '${betaRoot}'`
  )
  process.exit(1)
}

const pythonPath = resolvePythonExecutable()
if (!pythonPath) {
  console.error(
    '\n[FATAL PYTHON ERROR] Could not locate a working Python 3 executable for seeding.\n' +
    'Please ensure Python 3 is installed and accessible on PATH or set PYTHON_EXEC.\n'
  )
  process.exit(1)
}

console.log('[VEGA SEED] Seeding beta acceptance profile database...')
console.log(`[VEGA SEED] Python:    ${pythonPath}`)
console.log(`[VEGA SEED] Beta Root: ${betaRoot}`)
console.log(`[VEGA SEED] Target DB: ${betaDbPath}`)

const child = spawn(
  pythonPath,
  ['backend/seed_beta.py'],
  {
    cwd: rootDir,
    env,
    stdio: 'inherit',
  }
)

child.on('error', (err) => {
  console.error('[VEGA SEED] Failed to spawn seed subprocess:', err)
  process.exit(1)
})

child.on('exit', (code) => {
  process.exit(code || 0)
})
