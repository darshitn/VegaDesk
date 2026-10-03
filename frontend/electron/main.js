import { app, BrowserWindow, globalShortcut, Tray, Menu, ipcMain, screen, nativeImage, shell, session, Notification } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import AutoLaunch from 'auto-launch'

// ── Profile and Environment Detection ──────────────────────────────────────────
// Beta acceptance mode is explicitly opt-in via VEGA_PROFILE=beta or --profile=beta
const isBeta = process.env.VEGA_PROFILE === 'beta' || process.argv.includes('--profile=beta')
const currentPort = isBeta ? Number(process.env.VEGA_PORT || 8005) : Number(process.env.VEGA_PORT || 8000)
const runId = process.env.VEGA_RUN_ID || (isBeta ? `beta-${Date.now()}` : 'default')

const betaRoot = process.env.VEGA_PROFILE_ROOT || path.join(app.getPath('appData'), 'Jarvis_Dashboard_Beta')

if (isBeta) {
  process.env.VEGA_PROFILE = 'beta'
  process.env.VEGA_PORT = String(currentPort)
  process.env.VEGA_RUN_ID = runId
  process.env.VEGA_PROFILE_ROOT = betaRoot

  const betaUserData = path.join(betaRoot, 'userData')
  const betaDbDir = path.join(betaRoot, 'db')
  const betaLogsDir = path.join(betaRoot, 'logs')
  try {
    fs.mkdirSync(betaUserData, { recursive: true })
    fs.mkdirSync(betaDbDir, { recursive: true })
    fs.mkdirSync(betaLogsDir, { recursive: true })
  } catch (err) {
    console.error('[BETA SETUP] Failed to create beta directories:', err)
  }
  // MUST set userData path BEFORE loadSettings() and BEFORE requestSingleInstanceLock()
  app.setPath('userData', betaUserData)
}

if (process.platform === 'win32') {
  app.setAppUserModelId(isBeta ? 'com.jarvis.vega.beta' : 'com.jarvis.vega')
}

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']

let mainWindow
let tray
let backendProcess = null;
let jarvisAutoLauncher = null;
let isJustShown = false;
let isMaximizedManual = false;
let savedBounds = null;

// ── Persistent settings (userData/jarvis-settings.json) ──
// Theme, display mode and window geometry survive restarts, mirroring how the
// renderer keeps its own settings (name/city/provider) in localStorage.
const VALID_THEMES = ['sci-fi-hud', 'glass', 'terminal', 'neural-cosmos']
const VALID_MODES = ['Hotkey Overlay', 'Pinned Desktop']

let settingsPath = null
let boundsSaveTimer = null

function getSettingsPath() {
  if (!settingsPath) settingsPath = path.join(app.getPath('userData'), 'jarvis-settings.json')
  return settingsPath
}

function loadSettings() {
  const defaults = { theme: 'sci-fi-hud', mode: 'Hotkey Overlay', windowBounds: null, maximized: false }
  try {
    const parsed = JSON.parse(fs.readFileSync(getSettingsPath(), 'utf-8'))
    const merged = { ...defaults, ...parsed }
    if (!VALID_THEMES.includes(merged.theme)) merged.theme = defaults.theme
    if (!VALID_MODES.includes(merged.mode)) merged.mode = defaults.mode
    if (!merged.windowBounds || typeof merged.windowBounds.x !== 'number' ||
        typeof merged.windowBounds.y !== 'number' ||
        typeof merged.windowBounds.width !== 'number' ||
        typeof merged.windowBounds.height !== 'number') {
      merged.windowBounds = null
    }
    return merged
  } catch {
    return defaults // first run, or file missing/corrupt
  }
}

function saveSettings(patch) {
  try {
    const data = { ...loadSettings(), ...patch }
    fs.writeFileSync(getSettingsPath(), JSON.stringify(data, null, 2))
  } catch (err) {
    console.error('Failed to persist settings:', err)
  }
}

// Debounced bounds saving — resize/move fire continuously while dragging.
function scheduleBoundsSave(win) {
  if (boundsSaveTimer) clearTimeout(boundsSaveTimer)
  boundsSaveTimer = setTimeout(() => {
    boundsSaveTimer = null
    try {
      if (!win || win.isDestroyed() || win.isMaximized() || win.isFullScreen()) return
      saveSettings({ windowBounds: win.getBounds() })
    } catch {}
  }, 400)
}

function isBoundsVisible(b) {
  // Reject geometry from a monitor that no longer exists (at least 150x60px
  // of the window must land inside some display's work area).
  try {
    return screen.getAllDisplays().some(d => {
      const a = d.workArea
      const ix = Math.max(0, Math.min(b.x + b.width, a.x + a.width) - Math.max(b.x, a.x))
      const iy = Math.max(0, Math.min(b.y + b.height, a.y + a.height) - Math.max(b.y, a.y))
      return ix >= 150 && iy >= 60
    })
  } catch {
    return false
  }
}

const bootSettings = loadSettings()
let currentMode = bootSettings.mode // Options: 'Hotkey Overlay', 'Pinned Desktop'
let currentTheme = bootSettings.theme // Options: 'sci-fi-hud', 'glass', 'terminal', 'neural-cosmos'

// A simple cyan HUD dot for the tray icon
const iconBase64 = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAArUlEQVR4nN1VQQ6AIAyjxD/B/0/zVTMaiUQGbBg42AMHMtpuwObcZEAZR5X9+FWArpU5yKex94QwRGwQgoockI0wc08ETXJUiGtCgogXgm3keWynpHQ5ydO24jlP9QwmgZTuqfEnxCy80US469wWyeBN5AkGkel34JVxMXvj6b13+9CJzWAmOiCVRUX+r3/wPQsu3csZpMu0iHDR7B661e16ycBZMjLdC8NDfzoOErx58k+crzIAAAAASUVORK5CYII='

const trayIcon = nativeImage.createFromDataURL(iconBase64)

function applyWindowThemeConfig(win, theme) {
  if (!win || win.isDestroyed()) return;
  try {
    if (theme === 'glass') {
      // Windows 11 acrylic/mica requires these
      win.setBackgroundMaterial('acrylic');
      // macOS vibrancy
      win.setVibrancy('fullscreen-ui');
    } else {
      win.setBackgroundMaterial('none');
      win.setVibrancy(null);
    }
  } catch (err) {
    console.error("Failed to apply native window background material", err);
  }
}

function createMainWindow() {
  const savedBoundsBoot = (bootSettings.windowBounds && isBoundsVisible(bootSettings.windowBounds))
    ? bootSettings.windowBounds
    : null

  mainWindow = new BrowserWindow({
    title: isBeta ? `V.E.G.A. [BETA PROFILE · PORT ${currentPort}]` : 'V.E.G.A.',
    width: savedBoundsBoot ? savedBoundsBoot.width : 1100,
    height: savedBoundsBoot ? savedBoundsBoot.height : 750,
    ...(savedBoundsBoot ? { x: savedBoundsBoot.x, y: savedBoundsBoot.y } : {}),
    minWidth: 800,
    minHeight: 600,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: true,
    resizable: true,
    movable: true,
    fullscreenable: true,
    thickFrame: true,
    alwaysOnTop: true,
    center: true,
    skipTaskbar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      autoplayPolicy: 'no-user-gesture-required',
      backgroundThrottling: false
    }
  })

  if (VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(VITE_DEV_SERVER_URL + '#overlay')
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'), { hash: 'overlay' })
  }

  // On first load, show the window
  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
    mainWindow.webContents.send('toggle-visibility', true)
  })

  mainWindow.on('blur', () => {
    if (currentMode === 'Hotkey Overlay' && !isJustShown) {
      // Directly hide - simpler and more reliable than animation-then-hide
      mainWindow.hide()
    }
  })

  mainWindow.on('show', updateTrayMenu)
  mainWindow.on('hide', updateTrayMenu)
  // Restore maximized state from the previous session (the 'maximize' event
  // fires and re-syncs isMaximizedManual + the renderer).
  if (bootSettings.maximized) mainWindow.maximize()

  // Persist window geometry as it changes
  mainWindow.on('resize', () => scheduleBoundsSave(mainWindow))
  mainWindow.on('move', () => scheduleBoundsSave(mainWindow))

  mainWindow.on('maximize', () => {
    isMaximizedManual = true
    saveSettings({ maximized: true })
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('maximize-changed', true)
  })
  mainWindow.on('unmaximize', () => {
    isMaximizedManual = false
    saveSettings({ maximized: false })
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('maximize-changed', false)
  })
  mainWindow.on('enter-full-screen', () => {
    isMaximizedManual = true
    saveSettings({ maximized: true })
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('maximize-changed', true)
  })
  mainWindow.on('leave-full-screen', () => {
    isMaximizedManual = false
    saveSettings({ maximized: false })
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('maximize-changed', false)
  })

  applyWindowThemeConfig(mainWindow, currentTheme)

  // Register shortcut with error handling (scoped to beta to avoid personal profile collision)
  const shortcutKey = isBeta ? 'CommandOrControl+Alt+Space' : 'CommandOrControl+Space'
  const shortcutOk = globalShortcut.register(shortcutKey, () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    console.log('Shortcut pressed. isVisible:', mainWindow.isVisible());
    if (mainWindow.isVisible()) {
      // Notify React to play the exit animation, then hide after it completes
      try { mainWindow.webContents.send('toggle-visibility', false) } catch {}
      setTimeout(() => {
        if (!mainWindow || mainWindow.isDestroyed()) return
        console.log('Executing setTimeout hide()');
        try { mainWindow.hide() } catch {}
      }, 200)
    } else {
      console.log('Executing show() and focus()');
      isJustShown = true;
      try { mainWindow.setOpacity(0) } catch {}
      try { mainWindow.show() } catch {}
      try { mainWindow.focus() } catch {}
      try { mainWindow.webContents.send('toggle-visibility', true) } catch {}
      setTimeout(() => {
        if (!mainWindow || mainWindow.isDestroyed()) return
        try { mainWindow.setOpacity(1) } catch {}
        isJustShown = false
      }, 50)
    }
  })
  if (!shortcutOk) {
    console.warn(`Failed to register global shortcut ${shortcutKey} — may be already in use.`)
  }
}

function updateWindowsVisibility() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (currentMode === 'Pinned Desktop') {
      mainWindow.showInactive()
    } else if (currentMode === 'Hotkey Overlay' && !mainWindow.isFocused()) {
      mainWindow.hide()
    }
  }
}

function setupTray() {
  tray = new Tray(trayIcon)
  tray.setToolTip(isBeta ? `V.E.G.A. [BETA PROFILE · PORT ${currentPort}]` : 'V.E.G.A.')
  updateTrayMenu()
}

function updateTrayMenu() {
  const contextMenu = Menu.buildFromTemplate([
    {
      label: (mainWindow && mainWindow.isVisible()) ? 'Hide Dashboard' : 'Show Dashboard',
      click: () => {
        if (mainWindow.isVisible()) mainWindow.hide()
        else { mainWindow.show(); mainWindow.focus(); }
      }
    },
    { type: 'separator' },
    {
      label: 'Mode: Hotkey Overlay',
      type: 'radio',
      checked: currentMode === 'Hotkey Overlay',
      click: () => switchMode('Hotkey Overlay')
    },
    {
      label: 'Mode: Pinned Desktop',
      type: 'radio',
      checked: currentMode === 'Pinned Desktop',
      click: () => switchMode('Pinned Desktop')
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.quit()
      }
    }
  ])
  tray.setContextMenu(contextMenu)
}

function switchMode(newMode) {
  currentMode = newMode;
  saveSettings({ mode: newMode });
  updateWindowsVisibility();
  updateTrayMenu();
  
  // Notify renderers
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('mode-changed', newMode);
  }
}

function checkPortInUse(port) {
  return new Promise((resolve) => {
    const tester = net.createServer()
      .once('error', (err) => {
        if (err.code === 'EADDRINUSE') resolve(true)
        else resolve(false)
      })
      .once('listening', () => {
        tester.once('close', () => resolve(false)).close()
      })
      .listen(port, '127.0.0.1')
  })
}

function probeHealth(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 1500 }, (res) => {
      let data = ''
      res.on('data', chunk => { data += chunk })
      res.on('end', () => {
        if (res.statusCode !== 200) {
          return resolve({ ok: false, error: `HTTP ${res.statusCode}` })
        }
        try {
          resolve({ ok: true, data: JSON.parse(data) })
        } catch {
          resolve({ ok: false, error: 'malformed_json' })
        }
      })
    })
    req.on('error', (err) => resolve({ ok: false, error: err.message }))
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'timeout' }) })
  })
}

// Single instance: scoped to userData directory (isolated between default and beta profiles)
const gotTheLock = app.requestSingleInstanceLock({ profile: isBeta ? 'beta' : 'default' })
if (!gotTheLock) {
  console.log(`[VEGA ${isBeta ? 'BETA' : 'DEFAULT'}] Another instance is already running — exiting.`)
  app.quit()
} else {
  app.on('second-instance', () => {
    // Bring the existing dashboard to the front instead
    if (mainWindow && !mainWindow.isDestroyed()) {
      isJustShown = true
      try { mainWindow.setOpacity(0) } catch {}
      try { mainWindow.show() } catch {}
      try { mainWindow.focus() } catch {}
      try { mainWindow.webContents.send('toggle-visibility', true) } catch {}
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) { try { mainWindow.setOpacity(1) } catch {} }
        isJustShown = false
      }, 50)
    }
  })
}

app.whenReady().then(async () => {
  if (!gotTheLock) return // quit already requested

  // Port collision and ownership check
  const portBusy = await checkPortInUse(currentPort)
  if (portBusy) {
    const probe = await probeHealth(currentPort)
    const expectedProfile = isBeta ? 'beta' : 'default'
    const profileMatch = probe.ok && probe.data && probe.data.profile === expectedProfile
    const runIdMatch = !isBeta || (probe.ok && probe.data && probe.data.run_id === runId)
    const dbReady = probe.ok && probe.data && probe.data.database && probe.data.database.status === 'ready'

    if (!profileMatch || !runIdMatch || !dbReady) {
      console.error(
        `[VEGA PORT ERROR] Port ${currentPort} is occupied by an external service, mismatched profile, ` +
        `or mismatched session (profile=${probe?.data?.profile}, run_id=${probe?.data?.run_id}, ` +
        `db=${probe?.data?.database?.status}). Refusing to launch to prevent cross-profile data corruption.`
      )
      app.quit()
      return
    }
  }

  // Set up Auto-Launch only for normal mode (beta profile NEVER touches OS startup)
  if (!isBeta) {
    jarvisAutoLauncher = new AutoLaunch({
      name: 'Vega Dashboard',
      path: app.getPath('exe'),
    })
  }

  // Spawn bundled backend if packaged
  if (app.isPackaged) {
    if (portBusy) {
      console.log(`[VEGA PACKAGED] Port ${currentPort} already has an active verified backend. Skipping secondary spawn to prevent port collision.`)
    } else {
      const backendPath = path.join(process.resourcesPath, 'backend', 'jarvis-backend.exe');
      const backendDbPath = isBeta
        ? path.join(betaRoot, 'db', 'jarvis-beta.db')
        : path.join(app.getPath('userData'), 'jarvis.db')

      const backendEnv = {
        ...process.env,
        VEGA_PROFILE: isBeta ? 'beta' : (process.env.VEGA_PROFILE || 'default'),
        VEGA_PORT: String(currentPort),
        VEGA_RUN_ID: runId,
        JARVIS_DB_PATH: backendDbPath,
        VEGA_PROFILE_ROOT: isBeta ? betaRoot : app.getPath('userData'),
        VEGA_DISABLE_VOICE: isBeta ? '1' : (process.env.VEGA_DISABLE_VOICE || '0'),
        VEGA_DISABLE_RADAR: isBeta ? '1' : (process.env.VEGA_DISABLE_RADAR || '0'),
        LLM_PROVIDER: isBeta ? 'none' : (process.env.LLM_PROVIDER || 'gemini'),
      }

      try {
        if (fs.existsSync(backendPath)) {
          backendProcess = spawn(backendPath, [], { env: backendEnv, stdio: 'inherit' });
          backendProcess.on('error', (err) => console.error('Backend spawn error:', err))
          backendProcess.on('exit', (code) => console.log('Backend exited with code', code))
        } else {
          console.error('Bundled backend not found at', backendPath)
        }
      } catch (err) {
        console.error('Failed to spawn backend:', err)
      }
    }
  }

  createMainWindow()
  setupTray()

  const activeSession = session.defaultSession
  
  activeSession.setPermissionRequestHandler((webContents, permission, callback) => {
    if (permission === 'media' || permission === 'notifications') {
      callback(true)
    } else {
      callback(false)
    }
  })
  
  activeSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) => {
    if (permission === 'media' || permission === 'notifications') {
      return true
    }
    return false
  })

  ipcMain.on('hide-window', (e) => {
    const win = BrowserWindow.fromWebContents(e.sender)
    if (win) win.webContents.send('toggle-visibility', false)
  })

  ipcMain.on('hide-window-now', (e) => {
    const win = (e && e.sender) ? BrowserWindow.fromWebContents(e.sender) : null
    const target = win || mainWindow
    if (target && !target.isDestroyed()) {
      try { target.hide() } catch {}
    }
  })

  ipcMain.on('show-window', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      isJustShown = true;
      mainWindow.setOpacity(0);
      mainWindow.show()
      mainWindow.focus()
      mainWindow.webContents.send('toggle-visibility', true)
      setTimeout(() => { 
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.setOpacity(1);
        }
        isJustShown = false 
      }, 50)
    }
  })

  ipcMain.on('maximize-window', (e) => {
    const win = (e && e.sender) ? BrowserWindow.fromWebContents(e.sender) : mainWindow
    if (!win || win.isDestroyed()) return
    try {
      const isExpanded = win.isFullScreen() || isMaximizedManual
      if (isExpanded) {
        // Restore to the pre-expansion bounds
        try { win.setFullScreen(false) } catch (err) { console.error('exit fullscreen failed', err) }
        if (savedBounds) {
          try { win.setBounds(savedBounds, true) } catch (err) { console.error('restore bounds failed', err) }
        } else {
          try { win.unmaximize() } catch {}
          try { win.center() } catch {}
        }
        isMaximizedManual = false
        savedBounds = null
        try { win.webContents.send('maximize-changed', false) } catch {}
      } else {
        try { savedBounds = win.getBounds() } catch { savedBounds = null }
        try { win.setFullScreen(true) } catch (err) {
          console.error('fullscreen failed, falling back to work-area fill:', err)
          try {
            if (!savedBounds) savedBounds = win.getBounds()
            const area = screen.getDisplayNearestPoint({ x: savedBounds.x, y: savedBounds.y }).workArea
            win.setBounds({ x: area.x, y: area.y, width: area.width, height: area.height }, true)
          } catch (err2) { console.error('work-area fallback failed:', err2) }
        }
        isMaximizedManual = true
        try { win.webContents.send('maximize-changed', true) } catch {}
      }
    } catch (err) {
      console.error('Maximize toggle error:', err)
    }
  })

  ipcMain.handle('get-maximize-state', () => isMaximizedManual)

  ipcMain.on('set-auto-launch', (e, enabled) => {
    if (isBeta || !jarvisAutoLauncher) return
    if (enabled) {
      jarvisAutoLauncher.enable().catch(() => {});
    } else {
      jarvisAutoLauncher.disable().catch(() => {});
    }
  })

  ipcMain.handle('get-auto-launch', async () => {
    if (isBeta || !jarvisAutoLauncher) return false
    try {
      return await jarvisAutoLauncher.isEnabled();
    } catch (e) {
      return false;
    }
  })

  ipcMain.on('open-external', (e, url) => {
    if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
      shell.openExternal(url)
    }
  })

  ipcMain.on('switch-mode', (e, mode) => {
    switchMode(mode)
  })

  ipcMain.handle('get-mode', () => currentMode)
  
  ipcMain.on('switch-theme', (e, theme) => {
    currentTheme = theme;
    saveSettings({ theme });
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('theme-changed', theme);
      applyWindowThemeConfig(mainWindow, theme);
    }
  })

  ipcMain.handle('get-theme', () => currentTheme)

  ipcMain.on('get-profile-sync', (event) => {
    event.returnValue = {
      profile: isBeta ? 'beta' : 'default',
      port: currentPort,
      runId,
      isBeta,
      betaRoot: isBeta ? betaRoot : null,
    }
  })

  ipcMain.handle('get-profile-info', () => ({
    profile: isBeta ? 'beta' : 'default',
    port: currentPort,
    runId,
    isBeta,
    betaRoot: isBeta ? betaRoot : null,
  }))

  ipcMain.handle('show-notification', async (event, options) => {
    if (!Notification.isSupported()) {
      return { accepted: false, reason: 'unsupported' }
    }
    if (!options || typeof options !== 'object') {
      return { accepted: false, reason: 'invalid_options' }
    }
    try {
      const title = String(options.title || 'V.E.G.A. Alert').slice(0, 200)
      const body = String(options.body || '').slice(0, 1000)
      const notification = new Notification({
        title,
        body,
        icon: trayIcon
      })

      notification.on('click', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          isJustShown = true
          if (mainWindow.isMinimized()) mainWindow.restore()
          mainWindow.show()
          mainWindow.focus()
          mainWindow.webContents.send('toggle-visibility', true)
          mainWindow.webContents.send('notification-clicked', {
            id: options.id,
            kind: options.kind,
            entity_id: options.entity_id,
          })
          setTimeout(() => { isJustShown = false }, 50)
        }
      })

      notification.on('failed', (err) => {
        console.warn('[ELECTRON NOTIFICATION] OS notification failed event:', err)
      })

      notification.show()
      // Truthful return: API accepted and shown to OS notification subsystem;
      // does not claim user-observed Windows rendering.
      return {
        accepted: true,
        channel: 'electron-native',
        delivery_stage: 'api_accepted',
        observed_by_user: false
      }
    } catch (err) {
      console.error('[ELECTRON] Failed to show notification:', err)
      return { accepted: false, error: err?.message || String(err) }
    }
  })

  ipcMain.handle('is-notification-supported', () => {
    return Notification.isSupported()
  })

  updateWindowsVisibility()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  try {
    if (boundsSaveTimer) { clearTimeout(boundsSaveTimer); boundsSaveTimer = null }
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isMaximized() && !mainWindow.isFullScreen()) {
      saveSettings({ windowBounds: mainWindow.getBounds() })
    }
  } catch {}
  if (backendProcess && backendProcess.pid) {
    if (process.platform === 'win32') {
      // PyInstaller onefile spawns a child process; killing only the parent
      // leaves the real backend alive holding the port. Kill the owned process tree.
      try {
        spawn('taskkill', ['/PID', String(backendProcess.pid), '/T', '/F'], { stdio: 'ignore' })
      } catch {
        try { backendProcess.kill() } catch {}
      }
    } else {
      try { backendProcess.kill() } catch {}
    }
  }
})
