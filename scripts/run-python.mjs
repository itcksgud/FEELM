import { spawnSync } from 'node:child_process'

const isWindows = process.platform === 'win32'
const command = isWindows ? 'py' : 'python3'
const args = isWindows ? ['-3', ...process.argv.slice(2)] : process.argv.slice(2)
const result = spawnSync(command, args, { stdio: 'inherit' })

if (result.error) {
  console.error(result.error.message)
  process.exit(1)
}
process.exit(result.status ?? 1)
