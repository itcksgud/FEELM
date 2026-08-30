import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const dist = path.join(root, 'frontend', 'dist')
const forbidden = [
  '/__experiments/recommendation-interpretation',
  '추천 해석 실험',
  '직접 측정한 만족도가 아니에요',
  '예상 별점 (실험)',
  '실험 전용 · displayEligible=false',
]

if (!fs.existsSync(dist)) {
  console.error('C6 production bundle boundary: FAIL - frontend/dist is missing')
  process.exit(1)
}

const files = []
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) walk(target)
    else if (entry.isFile() && /\.(?:html|js|css)$/.test(entry.name)) files.push(target)
  }
}
walk(dist)

const leaks = []
for (const file of files) {
  const contents = fs.readFileSync(file, 'utf8')
  for (const marker of forbidden) {
    if (contents.includes(marker)) leaks.push(`${path.relative(root, file)}: ${marker}`)
  }
}

if (leaks.length > 0) {
  console.error('C6 production bundle boundary: FAIL')
  for (const leak of leaks) console.error(`- ${leak}`)
  process.exit(1)
}

console.log(`C6 production bundle boundary: PASS (${files.length} assets, local experiment route/copy absent)`)
