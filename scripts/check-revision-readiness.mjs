import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const requireReady = process.argv.includes('--require-ready')
const requiredPaths = [
  '.github/workflows/ci.yml',
  'AGENTS.md',
  'README.md',
  'backend/build.gradle.kts',
  'data-pipeline/pyproject.toml',
  'docker-compose.yml',
  'docs/planning/project-completion-gates.yaml',
  'frontend/package.json',
  'package-lock.json',
  'package.json',
  'recommender/pyproject.toml',
  'requirements-data.lock',
  'requirements-ml.lock',
  'scripts/verify-reproduction.ps1',
]

function git(args, options = {}) {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8', ...options })
}

let head = null
try {
  head = git(['rev-parse', 'HEAD']).trim()
} catch {
  // A repository without a revision is reported as not ready below.
}

const branch = git(['branch', '--show-current']).trim() || null
const tracked = new Set(git(['ls-files', '--cached']).split(/\r?\n/).filter(Boolean).map((item) => item.replaceAll('\\', '/')))
const missingRequiredPaths = requiredPaths.filter((item) => !tracked.has(item))
const statusEntries = git(['status', '--porcelain=v1', '--untracked-files=all'])
  .split(/\r?\n/)
  .filter(Boolean)
const modifiedTrackedCount = statusEntries.filter((line) => !line.startsWith('??')).length
const untrackedCount = statusEntries.filter((line) => line.startsWith('??')).length
const ready = Boolean(head) && missingRequiredPaths.length === 0 && statusEntries.length === 0

const result = {
  status: ready ? 'READY' : 'BLOCKED_REVISION_REQUIRED',
  branch,
  head,
  requiredPathCount: requiredPaths.length,
  trackedRequiredPathCount: requiredPaths.length - missingRequiredPaths.length,
  missingRequiredPaths,
  modifiedTrackedCount,
  untrackedCount,
  cleanWorkingTree: statusEntries.length === 0,
}

console.log(JSON.stringify(result))
if (requireReady && !ready) process.exit(1)
