/**
 * Pure, side-effect-free lifecycle, environment, and validation helpers
 * for VEGA AgentOS isolated profiles (beta acceptance launcher & seed entry point).
 *
 * Importing this module MUST NEVER spawn processes, bind ports, or launch applications.
 */

import net from 'node:net'
import http from 'node:http'
import path from 'node:path'
import os from 'node:os'
import crypto from 'node:crypto'
import { execFileSync } from 'node:child_process'

/**
 * Returns isolated filesystem paths for the beta acceptance profile.
 */
export function getBetaPaths(customRoot = null) {
  const defaultBetaRoot = process.env.APPDATA
    ? path.join(process.env.APPDATA, 'Jarvis_Dashboard_Beta')
    : path.join(os.homedir(), 'AppData', 'Roaming', 'Jarvis_Dashboard_Beta')

  const betaRoot = customRoot || process.env.VEGA_PROFILE_ROOT || defaultBetaRoot
  const betaDbPath = path.join(betaRoot, 'db', 'jarvis-beta.db')
  const betaUserData = path.join(betaRoot, 'userData')
  const betaLogsDir = path.join(betaRoot, 'logs')

  return {
    betaRoot,
    betaDbPath,
    betaUserData,
    betaLogsDir,
  }
}

/**
 * Prepares an isolated environment for the beta profile.
 * Strips cloud API keys and injects offline determinism flags.
 */
export function getBetaEnvironment(customRoot = null, runId = null, baseEnv = process.env) {
  const paths = getBetaPaths(customRoot)
  const actualRunId = runId || `beta-${crypto.randomUUID()}`
  const betaPort = 8005

  const env = {
    ...baseEnv,
    VEGA_PROFILE: 'beta',
    VEGA_PORT: String(betaPort),
    VEGA_RUN_ID: actualRunId,
    VEGA_PROFILE_ROOT: paths.betaRoot,
    JARVIS_DB_PATH: paths.betaDbPath,
    LLM_PROVIDER: 'none',
    VEGA_DISABLE_VOICE: '1',
    VEGA_DISABLE_RADAR: '1',
  }

  // Discard inherited cloud API keys to guarantee deterministic isolation
  delete env.GEMINI_API_KEY
  delete env.OPENAI_API_KEY
  delete env.ANTHROPIC_API_KEY
  delete env.GROQ_API_KEY
  delete env.MISTRAL_API_KEY

  return {
    env,
    runId: actualRunId,
    betaPort,
    betaRoot: paths.betaRoot,
    betaDbPath: paths.betaDbPath,
  }
}

/**
 * Tests whether a TCP port is currently free on 127.0.0.1.
 */
export function checkPortAvailable(port, host = '127.0.0.1', netModule = net) {
  return new Promise((resolve) => {
    const server = netModule.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close(() => resolve(true))
    })
    server.listen(port, host)
  })
}

/**
 * Resolves a valid Python 3 executable on the system.
 */
export function resolvePythonExecutable(options = {}) {
  const env = options.env || process.env
  const execFn = options.execFileSync || execFileSync
  const candidates = []

  if (options.pythonExec) {
    candidates.push(options.pythonExec)
  }
  if (env.PYTHON_EXEC) {
    candidates.push(env.PYTHON_EXEC)
  }
  if (env.VIRTUAL_ENV) {
    candidates.push(path.join(env.VIRTUAL_ENV, 'Scripts', 'python.exe'))
    candidates.push(path.join(env.VIRTUAL_ENV, 'bin', 'python'))
  }
  candidates.push('python', 'py', 'python3')

  for (const candidate of candidates) {
    try {
      const output = execFn(candidate, ['-c', 'import sys; print(sys.executable)'], {
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'ignore'],
        windowsHide: true,
      })
      if (typeof output === 'string') {
        const trimmed = output.trim()
        if (trimmed && trimmed.toLowerCase().includes('python')) {
          return trimmed
        }
      }
    } catch {}
  }
  return null
}

/**
 * Performs a single HTTP GET /health probe and checks HTTP 200, profile, run_id, and DB readiness.
 */
export function probeHealth(
  port,
  expectedRunId,
  expectedProfile = 'beta',
  timeoutMs = 800,
  httpModule = http
) {
  return new Promise((resolve) => {
    const req = httpModule.get(
      `http://127.0.0.1:${port}/health`,
      { timeout: timeoutMs },
      (res) => {
        let body = ''
        res.on('data', (chunk) => { body += chunk })
        res.on('end', () => {
          if (res.statusCode !== 200) {
            return resolve({ ok: false, reason: `HTTP ${res.statusCode}` })
          }
          try {
            const data = JSON.parse(body)
            if (expectedProfile && data.profile !== expectedProfile) {
              return resolve({ ok: false, reason: `mismatched profile '${data.profile}'` })
            }
            if (expectedRunId && data.run_id !== expectedRunId) {
              return resolve({ ok: false, reason: `mismatched run_id '${data.run_id}'` })
            }
            if (!data.database || data.database.status !== 'ready') {
              return resolve({ ok: false, reason: 'database not ready' })
            }
            return resolve({ ok: true, data })
          } catch {
            return resolve({ ok: false, reason: 'malformed JSON' })
          }
        })
      }
    )
    req.on('error', (err) => resolve({ ok: false, reason: err?.message || 'network error' }))
    req.on('timeout', () => {
      req.destroy()
      resolve({ ok: false, reason: 'timeout' })
    })
  })
}

/**
 * Bounded sleep helper.
 */
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Cleanly terminates a child process and its process tree without killing unrelated processes.
 */
export function killProcessTree(child, execFn = execFileSync) {
  if (!child || !child.pid) return
  if (process.platform === 'win32') {
    try {
      execFn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      })
    } catch {
      try { child.kill('SIGKILL') } catch {}
    }
  } else {
    try { child.kill('SIGTERM') } catch {}
  }
}
