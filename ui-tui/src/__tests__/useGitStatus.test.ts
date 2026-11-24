import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  MAX_UNTRACKED_FILE_BYTES, MAX_UNTRACKED_FILES, MAX_UNTRACKED_TOTAL_LINES, resolveGitStatus, useGitStatus
} from '../hooks/useGitStatus.js'

const dirs: string[] = []

const git = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim()

const initRepo = () => {
  const cwd = mkdtempSync(join(tmpdir(), 'hermes-tui-git-'))
  dirs.push(cwd)
  git(cwd, 'init', '-b', 'main')
  writeFileSync(join(cwd, 'staged.txt'), 'one\n')
  writeFileSync(join(cwd, 'unstaged.txt'), 'one\n')
  git(cwd, 'add', '.')
  git(cwd, '-c', 'user.name=Hermes Test', '-c', 'user.email=hermes@example.invalid', 'commit', '-m', 'initial')

  return cwd
}

afterEach(() => {
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { force: true, recursive: true })
  }
})

describe('resolveGitStatus', () => {
  it('counts untracked files from a nested working directory', async () => {
    const root = initRepo()
    const cwd = join(root, 'sub')
    mkdirSync(cwd)
    writeFileSync(join(cwd, 'new.txt'), 'one\ntwo\n')
    await expect(resolveGitStatus(root)).resolves.toMatchObject({ files: 1, lines: 2 })
    await expect(resolveGitStatus(cwd)).resolves.toMatchObject({ files: 1, lines: 2 })
  })

  it('reports the branch and staged, unstaged, and untracked changes', async () => {
    const cwd = initRepo()
    writeFileSync(join(cwd, 'staged.txt'), 'one\ntwo\n')
    git(cwd, 'add', 'staged.txt')
    writeFileSync(join(cwd, 'unstaged.txt'), 'one\ntwo\nthree\n')
    writeFileSync(join(cwd, 'untracked.txt'), 'alpha\nbeta\n')
    writeFileSync(join(cwd, 'untracked.bin'), Buffer.from([0, 1, 2]))

    await expect(resolveGitStatus(cwd)).resolves.toEqual({
      branch: 'main',
      files: 4,
      isRepo: true,
      lines: 5
    })
  })

  it('bounds untracked file reads and reports the line cap conservatively', async () => {
    const cwd = initRepo()
    const huge = new Uint8Array(MAX_UNTRACKED_FILE_BYTES + 1).fill(97)
    huge.fill(10, MAX_UNTRACKED_FILE_BYTES - 1)
    writeFileSync(join(cwd, 'huge.txt'), huge)

    await expect(resolveGitStatus(cwd)).resolves.toMatchObject({
      files: 1,
      lines: MAX_UNTRACKED_TOTAL_LINES
    })
  })

  it('caps the result when the untracked file list is too large', async () => {
    const cwd = initRepo()

    for (let idx = 0; idx <= MAX_UNTRACKED_FILES; idx += 1) {
      writeFileSync(join(cwd, `untracked-${idx}.txt`), 'line\n')
    }

    await expect(resolveGitStatus(cwd)).resolves.toMatchObject({
      files: MAX_UNTRACKED_FILES + 1,
      lines: MAX_UNTRACKED_TOTAL_LINES
    })
  })

  it('omits the branch when HEAD is detached', async () => {
    const cwd = initRepo()
    git(cwd, 'checkout', '--detach')

    await expect(resolveGitStatus(cwd)).resolves.toMatchObject({ branch: null, isRepo: true })
  })

  it('counts staged and unstaged deltas even when the worktree matches HEAD', async () => {
    const cwd = initRepo()
    writeFileSync(join(cwd, 'staged.txt'), 'one\ntwo\n')
    git(cwd, 'add', 'staged.txt')
    writeFileSync(join(cwd, 'staged.txt'), 'one\n')

    await expect(resolveGitStatus(cwd)).resolves.toMatchObject({ files: 1, lines: 2 })
  })

  it('returns an empty non-repository state when git probing fails', async () => {
    const cwd = mkdtempSync(join(tmpdir(), 'hermes-tui-no-git-'))
    dirs.push(cwd)

    await expect(resolveGitStatus(cwd)).resolves.toEqual({
      branch: null,
      files: 0,
      isRepo: false,
      lines: 0
    })
  })
})

it('never renders a branch from the previous cwd while switching repositories', async () => {
  const first = initRepo()
  const second = initRepo()
  git(second, 'branch', '-m', 'other')
  const observed: Array<string | null> = []
  const Probe = ({ cwd }: { cwd: string }) => {
    observed.push(useGitStatus(cwd).branch)

    return null
  }
  const stdout = Object.assign(new PassThrough(), { columns: 100, isTTY: false, rows: 20 })
  const instance = renderSync(createElement(Probe, { cwd: first }), {
    patchConsole: false,
    stderr: new PassThrough() as NodeJS.WriteStream,
    stdin: new PassThrough() as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })
  try {
    await vi.waitFor(() => expect(observed.at(-1)).toBe('main'), { timeout: 3_000 })
    observed.length = 0
    instance.rerender(createElement(Probe, { cwd: second }))
    await vi.waitFor(() => expect(observed.at(-1)).toBe('other'), { timeout: 3_000 })
    expect(observed[0]).toBeNull()
    expect(observed).not.toContain('main')
  } finally {
    instance.unmount()
    instance.cleanup()
  }
})
