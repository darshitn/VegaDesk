/** Isolated Windows release build. No installs and no reads of personal data. */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolvePythonExecutable } from './launcherHelpers.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
const dirOnly = args.includes('--dir')
const backendOnly = args.includes('--backend-only')
const outputIndex = args.indexOf('--output-root')
if (args.some((arg, index) => !['--dir', '--backend-only', '--output-root'].includes(arg) &&
    !(outputIndex >= 0 && index === outputIndex + 1))) {
  throw new Error('Usage: node scripts/build_release.js [--dir | --backend-only] [--output-root PATH]')
}
if (dirOnly && backendOnly) throw new Error('Choose either --dir or --backend-only.')
if (outputIndex >= 0 && (!args[outputIndex + 1] || args[outputIndex + 1].startsWith('--'))) {
  throw new Error('--output-root needs a path.')
}
if (process.platform !== 'win32') throw new Error('This release target requires Windows.')

const version = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).version
const frontendVersion = JSON.parse(fs.readFileSync(path.join(root, 'frontend/package.json'), 'utf8')).version
if (version !== frontendVersion || !/^\d+\.\d+\.\d+-beta\.\d+$/.test(version)) {
  throw new Error(`Release versions must match and be beta versions (root=${version}, frontend=${frontendVersion}).`)
}
const outputRoot = outputIndex >= 0
  ? path.resolve(args[outputIndex + 1])
  : path.join(root, 'release')
const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '')
const releaseDir = path.join(outputRoot, version, `${stamp}_${crypto.randomUUID().slice(0, 8)}`)
const stage = path.join(releaseDir, 'stage')
const stageBackend = path.join(stage, 'backend')
const stageFrontend = path.join(stage, 'frontend')
const packageOutput = path.join(releaseDir, 'package')
const sourceHashes = []

function hashFile(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

function copySource(relative) {
  const source = path.join(root, relative)
  const target = path.join(stage, relative)
  if (!fs.statSync(source).isFile()) throw new Error(`Expected source file: ${relative}`)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.copyFileSync(source, target)
  sourceHashes.push({ path: relative.replaceAll('\\', '/'), sha256: hashFile(source) })
}

function copyTree(relative, allowedExtensions) {
  const sourceDir = path.join(root, relative)
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error(`Source symlink refused: ${path.join(relative, entry.name)}`)
    const child = path.join(relative, entry.name)
    if (entry.isDirectory()) copyTree(child, allowedExtensions)
    else if (entry.isFile() && allowedExtensions.has(path.extname(entry.name).toLowerCase())) copySource(child)
  }
}

function run(label, executable, commandArgs, cwd) {
  console.log(`[release] ${label}`)
  const result = spawnSync(executable, commandArgs, { cwd, stdio: 'inherit', windowsHide: true })
  if (result.error) throw new Error(`${label} could not start: ${result.error.message}`)
  if (result.status !== 0) throw new Error(`${label} failed (exit ${result.status ?? 'unknown'}). Output kept at ${releaseDir}`)
}

function requireFile(file, reason) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) throw new Error(`${reason}: ${file}`)
}

// Resolve tools before writing anything; dependency installation is deliberately external.
const python = resolvePythonExecutable()
if (!python) throw new Error('Python 3 executable not found. Set PYTHON_EXEC to an installed interpreter.')
const pyinstaller = spawnSync(python, ['-m', 'PyInstaller', '--version'], { stdio: 'pipe', encoding: 'utf8', windowsHide: true })
if (pyinstaller.status !== 0) throw new Error(`PyInstaller is missing from ${python}. Install project requirements in that interpreter before building.`)
if (!backendOnly) {
  requireFile(path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'Vite is not installed')
  requireFile(path.join(root, 'frontend/node_modules/electron-builder/cli.js'), 'electron-builder is not installed')
}

fs.mkdirSync(stageBackend, { recursive: true })
for (const entry of fs.readdirSync(path.join(root, 'backend'), { withFileTypes: true })) {
  if (entry.isFile() && path.extname(entry.name) === '.py') copySource(path.join('backend', entry.name))
}
copyTree('backend/providers', new Set(['.py']))
copySource('backend/jarvis-backend.spec')

if (!backendOnly) {
  copyTree('frontend/src', new Set(['.js', '.jsx', '.css', '.svg']))
  copyTree('frontend/electron', new Set(['.js']))
  copyTree('frontend/public', new Set(['.svg', '.png', '.jpg', '.webp']))
  for (const file of ['package.json', 'index.html', 'vite.config.js', 'postcss.config.js']) {
    copySource(path.join('frontend', file))
  }
  // Installed dependencies are referenced in place; they are never copied or modified.
  fs.symlinkSync(path.join(root, 'frontend/node_modules'), path.join(stageFrontend, 'node_modules'), 'junction')
}

sourceHashes.sort((a, b) => a.path.localeCompare(b.path))
fs.writeFileSync(path.join(releaseDir, 'source-manifest.json'), JSON.stringify({
  version, created_utc: new Date().toISOString(),
  note: 'Only whitelisted source files are hashed. No credentials, databases, audio, or model assets are included.',
  files: sourceHashes,
}, null, 2) + '\n')

run('PyInstaller backend (maintained spec)', python, [
  '-m', 'PyInstaller', '--noconfirm',
  '--distpath', path.join(stage, 'dist'),
  '--workpath', path.join(stage, 'build'),
  path.join(stageBackend, 'jarvis-backend.spec'),
], stageBackend)
requireFile(path.join(stage, 'dist', 'jarvis-backend.exe'), 'Backend executable missing after PyInstaller')

if (!backendOnly) {
  run('Vite renderer and Electron bundle', process.execPath,
    [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'build'], stageFrontend)
  for (const file of ['dist/index.html', 'dist-electron/main.js', 'dist-electron/preload.js']) {
    requireFile(path.join(stageFrontend, file), 'Frontend build output missing')
  }
  // The builder reads staged metadata. Redirect its output without touching the source package.
  const packageJsonPath = path.join(stageFrontend, 'package.json')
  const stagedPackage = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'))
  stagedPackage.build.directories.output = packageOutput
  fs.writeFileSync(packageJsonPath, JSON.stringify(stagedPackage, null, 2) + '\n')
  const builder = path.join(root, 'frontend/node_modules/electron-builder/cli.js')
  run('Unpacked Windows validation build', process.execPath, [builder, '--win', '--dir'], stageFrontend)
  requireFile(path.join(packageOutput, 'win-unpacked', 'VEGA Desk.exe'), 'Unpacked desktop executable missing')
  requireFile(path.join(packageOutput, 'win-unpacked', 'resources', 'backend', 'jarvis-backend.exe'),
    'Bundled backend missing at Electron startup path')
  if (!dirOnly) run('Windows installer', process.execPath, [builder, '--win'], stageFrontend)
}

console.log(`[release] Complete: ${releaseDir}`)
console.log('[release] Voice models and personal settings are external runtime assets; test native voice separately.')
