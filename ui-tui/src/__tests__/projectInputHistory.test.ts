import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { afterEach, expect, it, vi } from 'vitest'

const keyA = 'a'.repeat(64)
const keyB = 'b'.repeat(64)

async function fixture() {
  const home = mkdtempSync(resolve('../tmp/project-input-history/storage-'))
  vi.stubEnv('HERMES_HOME', home)
  vi.resetModules()

  return { home, history: await import('../lib/history.js') }
}

afterEach(() => vi.unstubAllEnvs())

it('isolates project caches and persists each project across fresh module loads', async () => {
  const { home, history } = await fixture()
  history.append('project A input', keyA)
  expect(history.load(keyA)).toEqual(['project A input'])
  expect(history.load(keyB)).toEqual([])
  history.append('project B input', keyB)
  history.append('project A multiline\n\nlast line', keyA)
  history.append('project A multiline\n\nlast line', keyA)
  expect(history.load(keyB)).toEqual(['project B input'])

  vi.resetModules()
  const reloaded = await import('../lib/history.js')
  expect(reloaded.load(keyA)).toEqual(['project A input', 'project A multiline\n\nlast line'])
  expect(reloaded.load(keyB)).toEqual(['project B input'])
  expect(statSync(join(home, 'input-history')).mode & 0o777).toBe(0o700)
  expect(statSync(join(home, 'input-history', keyA)).mode & 0o777).toBe(0o600)
})

it('never imports or modifies mixed legacy history and fails closed without project metadata', async () => {
  const { home, history } = await fixture()
  const legacy = join(home, '.hermes_history')
  const original = '\n# synthetic timestamp\n+foreign legacy input\n'
  writeFileSync(legacy, original)

  expect(history.load(null)).toEqual([])
  expect(history.load(keyA)).toEqual([])
  history.append('unscoped input', null)
  history.append('project A input', keyA)
  expect(history.load(null)).toEqual([])
  expect(history.load(keyA)).toEqual(['project A input'])
  expect(readFileSync(legacy, 'utf8')).toBe(original)
  expect(() => history.load('../foreign-history')).toThrow('Invalid project input history key')
})

it('reads the prompt_toolkit file format without mixing profile homes', async () => {
  const { home, history } = await fixture()
  mkdirSync(join(home, 'input-history'), { mode: 0o700 })
  writeFileSync(join(home, 'input-history', keyA), '\n# synthetic timestamp\n+CLI first line\n+\n+CLI last line\n')
  expect(history.load(keyA)).toEqual(['CLI first line\n\nCLI last line'])

  const otherHome = mkdtempSync(resolve('../tmp/project-input-history/other-profile-'))
  vi.stubEnv('HERMES_HOME', otherHome)
  expect(history.load(keyA)).toEqual([])
  history.append('other profile input', keyA)
  vi.stubEnv('HERMES_HOME', home)
  expect(history.load(keyA)).toEqual(['CLI first line\n\nCLI last line'])
})
