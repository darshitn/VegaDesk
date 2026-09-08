import { app, BrowserWindow, globalShortcut, Tray, Menu, ipcMain, screen, nativeImage, shell, session } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import AutoLaunch from 'auto-launch'

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

  // Register shortcut with error handling
  const shortcutOk = globalShortcut.register('CommandOrControl+Space', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    console.log('Shortcut pressed. isVisible:', mainWindow.isVisible());
    if (mainWindow.isVisible()) {
      // Notify React to play the exit animation, then hide after it completes
      try { mainWindow.webContents.send('toggle-visibility', false) } catch {}
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          console.log('Executing setTimeout hide()');
          try { mainWindow.hide() } catch {}
        }
      }, 200)
    } else {
      console.log('Executing show() and focus()');
      isJustShown = true;
      try { mainWindow.setOpacity(0) } catch {}
      try { mainWindow.show() } catch {}
      try { mainWindow.focus() } catch {}
      try { mainWindow.webContents.send('toggle-visibility', true) } catch {}
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          try { mainWindow.setOpacity(1) } catch {}
        }
        isJustShown = false
      }, 50)
    }
  })
  if (!shortcutOk) {
    console.error('Failed to register global shortcut CommandOrControl+Space — may be already in use.')
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
  tray.setToolTip('V.E.G.A.')
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

// Single instance: a second launch would spawn a second backend (port clash)
// and a second voice client (duplicate wake-word commands).
const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  console.log('[VEGA] Another instance is already running — exiting.')
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

app.whenReady().then(() => {
  if (!gotTheLock) return // quit already requested
  // Set up Auto-Launch
  jarvisAutoLauncher = new AutoLaunch({
      name: 'Vega Dashboard',
      path: app.getPath('exe'),
  });

  // Spawn bundled backend if packaged
  if (app.isPackaged) {
    const backendPath = path.join(process.resourcesPath, 'backend', 'jarvis-backend.exe');
    try {
      if (fs.existsSync(backendPath)) {
        backendProcess = spawn(backendPath, [], { stdio: 'inherit' });
        backendProcess.on('error', (err) => console.error('Backend spawn error:', err))
        backendProcess.on('exit', (code) => console.log('Backend exited with code', code))
      } else {
        console.error('Bundled backend not found at', backendPath)
      }
    } catch (err) {
      console.error('Failed to spawn backend:', err)
    }
  }

  createMainWindow()
  setupTray()

  const activeSession = session.defaultSession
  
  activeSession.setPermissionRequestHandler((webContents, permission, callback) => {
    if (permission === 'media') {
      callback(true)
    } else {
      callback(false)
    }
  })
  
  activeSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) => {
    if (permission === 'media') {
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
    if (enabled) {
      jarvisAutoLauncher.enable().catch(() => {});
    } else {
      jarvisAutoLauncher.disable().catch(() => {});
    }
  })

  ipcMain.handle('get-auto-launch', async () => {
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
  if (backendProcess) {
    if (process.platform === 'win32') {
      // PyInstaller onefile spawns a child process; killing only the parent
      // leaves the real backend alive holding port 8000. Kill the whole tree.
      try {
        spawn('taskkill', ['/PID', String(backendProcess.pid), '/T', '/F'], { stdio: 'ignore' })
      } catch {
        try { backendProcess.kill() } catch {}
      }
    } else {
      backendProcess.kill()
    }
  }
})
