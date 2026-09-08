import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import renderer from 'vite-plugin-electron-renderer'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: 'electron/main.js',
        onstart(options) {
          options.startup()
        },
      },
      {
        entry: 'electron/preload.js',
        onstart(options) {
          options.reload()
        },
        // Emit the preload as CommonJS: sandboxed preload scripts are always
        // evaluated as CJS, so the default ESM output fails to load and
        // window.electronAPI silently disappears in built runs. lib.formats
        // must be overridden too — Vite library mode derives the output
        // format from it, not from rolldownOptions.output.format.
        vite: {
          build: {
            lib: { formats: ['cjs'] },
            rolldownOptions: {
              output: {
                format: 'cjs',
              },
            },
          },
        },
      },
    ]),
    renderer(),
  ],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        advancedChunks: {
          groups: [
            { name: 'three', test: /node_modules[\\/](three|@react-three)[\\/]/ },
            { name: 'react-vendor', test: /node_modules[\\/](react|react-dom|framer-motion|scheduler)[\\/]/ },
          ],
        },
      },
    },
  },
  clearScreen: false,
})
