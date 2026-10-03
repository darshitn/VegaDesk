/**
 * Isolated Beta Acceptance Launcher for VEGA AgentOS.
 *
 * Guarantees:
 * 1. Single owned session: generates opaque run_id shared with backend, Electron, and renderer.
 * 2. Pre-launch refusal if port 8005 is occupied.
 * 3. Strict Python interpreter resolution with actionable error reporting.
 * 4. Bounded backend readiness wait: verifies HTTP 200, profile=beta, run_id match, and db readiness.
 * 5. Clean owned process-tree teardown on Windows without global process kills.
 * 6. Symmetric lifecycle: child failure or exit triggers clean teardown.
 */

import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  getBetaEnvironment,
  checkPortAvailable,
  resolvePythonExecutable,
  probeHealth,
  killProcessTree,
  sleep,
} from './launcherHelpers.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')

const { env, runId, betaPort, betaRoot, betaDbPath } = getBetaEnvironment()

let backendChild = null
let frontendChild = null
let isShuttingDown = false

function shutdown(exitCode = 0) {
  if (isShuttingDown) return
  isShuttingDown = true
  console.log('\n[VEGA LAUNCHER] Shutting down beta test processes...')
  killProcessTree(frontendChild)
  killProcessTree(backendChild)
  setTimeout(() => process.exit(exitCode), 200)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

async function main() {
  console.log('='.repeat(70))
  console.log('VEGA AGENTOS — BETA ACCEPTANCE LAUNCHER')
  console.log('='.repeat(70))
  console.log(`Profile:       beta`)
  console.log(`Port:          ${betaPort}`)
  console.log(`Run ID:        ${runId}`)
  console.log(`Data Root:     ${betaRoot}`)
  console.log(`Target DB:     ${betaDbPath}`)
  console.log(`Mode:          Deterministic Offline Only (Cloud & Voice disabled)`)
  console.log('='.repeat(70))

  // 1. Port Availability Check
  const portAvailable = await checkPortAvailable(betaPort)
  if (!portAvailable) {
    console.error(
      `\n[FATAL PORT ERROR] Port ${betaPort} is already in use by another process.\n` +
      `Beta acceptance requires an exclusive port. Please terminate existing processes on port ${betaPort} before launching.\n`
    )
    process.exit(1)
  }

  // 2. Python Resolution
  const pythonPath = resolvePythonExecutable()
  if (!pythonPath) {
    console.error(
      '\n[FATAL PYTHON ERROR] Could not locate a working Python 3 executable.\n' +
      'Please ensure Python 3 is installed and accessible on PATH or set PYTHON_EXEC.\n'
    )
    process.exit(1)
  }
  console.log(`[VEGA LAUNCHER] Resolved Python: ${pythonPath}`)

  // 3. Spawn Backend
  console.log('[VEGA LAUNCHER] Spawning backend...')
  const backendDir = path.join(rootDir, 'backend')
  const pythonPathEnv = [backendDir, rootDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  backendChild = spawn(
    pythonPath,
    ['-m', 'uvicorn', 'backend.main:app', '--port', String(betaPort)],
    {
      cwd: rootDir,
      env: {
        ...env,
        PYTHONPATH: pythonPathEnv,
      },
      stdio: ['ignore', 'inherit', 'inherit'],
    }
  )

  backendChild.on('error', (err) => {
    console.error('[VEGA LAUNCHER] Backend process error:', err)
    shutdown(1)
  })

  backendChild.on('exit', (code) => {
    if (!isShuttingDown) {
      console.error(`[VEGA LAUNCHER] Backend process unexpectedly exited with code ${code}`)
      shutdown(code || 1)
    }
  })

  // 4. Bounded Backend Readiness Wait
  console.log('[VEGA LAUNCHER] Awaiting backend readiness and identity verification...')
  const deadline = Date.now() + 15000 // 15 seconds
  let ready = false

  while (Date.now() < deadline) {
    if (backendChild.exitCode !== null) {
      console.error('[VEGA LAUNCHER] Backend terminated during startup wait.')
      shutdown(1)
      return
    }
    const probe = await probeHealth(betaPort, runId)
    if (probe.ok) {
      ready = true
      break
    }
    await sleep(250)
  }

  if (!ready) {
    console.error(
      '\n[FATAL TIMEOUT] Backend failed to achieve healthy readiness within 15 seconds.\n' +
      'Terminating launch to prevent orphaned execution.\n'
    )
    shutdown(1)
    return
  }

  console.log(`[VEGA LAUNCHER] Backend verified ready! (profile=beta, run_id=${runId})`)

  // 5. Spawn Frontend
  console.log('[VEGA LAUNCHER] Spawning frontend dev server and Electron...')
  frontendChild = spawn('npm', ['run', 'dev', '--prefix', 'frontend'], {
    cwd: rootDir,
    env,
    stdio: 'inherit',
    shell: process.platform === 'win32',
  })

  frontendChild.on('error', (err) => {
    console.error('[VEGA LAUNCHER] Frontend process error:', err)
    shutdown(1)
  })

  frontendChild.on('exit', (code) => {
    if (!isShuttingDown) {
      console.log(`[VEGA LAUNCHER] Frontend closed (code ${code}). Initiating graceful teardown...`)
      shutdown(code || 0)
    }
  })
}

main().catch((err) => {
  console.error('[VEGA LAUNCHER] Unexpected error:', err)
  shutdown(1)
})
