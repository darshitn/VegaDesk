import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  hideWindow: () => ipcRenderer.send('hide-window'),
  openExternal: (url) => ipcRenderer.send('open-external', url),
  switchMode: (mode) => ipcRenderer.send('switch-mode', mode),
  getMode: () => ipcRenderer.invoke('get-mode'),
  onModeChanged: (callback) => {
    const listener = (event, value) => callback(value);
    ipcRenderer.on('mode-changed', listener);
    return () => {
      ipcRenderer.removeListener('mode-changed', listener);
    };
  },
  switchTheme: (theme) => ipcRenderer.send('switch-theme', theme),
  getTheme: () => ipcRenderer.invoke('get-theme'),
  onThemeChanged: (callback) => {
    const listener = (event, value) => callback(value);
    ipcRenderer.on('theme-changed', listener);
    return () => {
      ipcRenderer.removeListener('theme-changed', listener);
    };
  },
  onToggleVisibility: (callback) => {
    const listener = (event, visible) => callback(visible);
    ipcRenderer.on('toggle-visibility', listener);
    return () => {
      ipcRenderer.removeListener('toggle-visibility', listener);
    };
  },
  hideWindowNow: () => ipcRenderer.send('hide-window-now'),
  showWindow: () => ipcRenderer.send('show-window'),
  maximizeWindow: () => ipcRenderer.send('maximize-window'),
  getMaximizeState: () => ipcRenderer.invoke('get-maximize-state'),
  onMaximizeChanged: (callback) => {
    const listener = (event, value) => callback(value);
    ipcRenderer.on('maximize-changed', listener);
    return () => {
      ipcRenderer.removeListener('maximize-changed', listener);
    };
  },
  setAutoLaunch: (enabled) => ipcRenderer.send('set-auto-launch', enabled),
  getAutoLaunch: () => ipcRenderer.invoke('get-auto-launch'),
  showNotification: (options) => ipcRenderer.invoke('show-notification', options),
  isNotificationSupported: () => ipcRenderer.invoke('is-notification-supported'),
  onNotificationClicked: (callback) => {
    const listener = (event, value) => callback(value);
    ipcRenderer.on('notification-clicked', listener);
    return () => {
      ipcRenderer.removeListener('notification-clicked', listener);
    };
  },
  getProfileInfo: () => ipcRenderer.sendSync('get-profile-sync'),
  getProfileInfoAsync: () => ipcRenderer.invoke('get-profile-info')
})
