import { execFile } from 'node:child_process'
import { open } from 'node:fs/promises'
import { resolve } from 'node:path'
import { promisify } from 'node:util'

import { useEffect, useState } from 'react'

const TTL_MS = 15_000
const TIMEOUT_MS = 800
const pexec = promisify(execFile)

export interface GitStatus {
  branch: null | string
  files: number
  isRepo: boolean
  lines: number
}

const EMPTY: GitStatus = { branch: null, files: 0, isRepo: false, lines: 0 }
const cache = new Map<string, { at: number; status: GitStatus }>()
const inflight = new Map<string, Promise<GitStatus>>()

const runGit = async (cwd: string, args: string[]): Promise<null | string> => {
  try {
    const { stdout } = await pexec('git', ['-C', cwd, ...args], {
      maxBuffer: 4 * 1024 * 1024,
      timeout: TIMEOUT_MS
    })

    return stdout
  } catch {
    return null
  }
}

const numstatLines = (output: null | string) => {
  if (!output) {
    return 0
  }

  return output.split('\n').reduce((total, row) => {
    const [added, deleted] = row.split('\t', 2)
    const additions = Number.parseInt(added ?? '', 10)
    const deletions = Number.parseInt(deleted ?? '', 10)

    return total + (Number.isFinite(additions) ? additions : 0) + (Number.isFinite(deletions) ? deletions : 0)
  }, 0)
}

export const MAX_UNTRACKED_FILES = 256
export const MAX_UNTRACKED_FILE_BYTES = 256 * 1024
export const MAX_UNTRACKED_TOTAL_BYTES = 4 * 1024 * 1024
export const MAX_UNTRACKED_FILE_LINES = 10_000
export const MAX_UNTRACKED_TOTAL_LINES = 100_000

const untrackedTextLines = async (cwd: string, paths: string[]) => {
  let total = 0
  let totalBytes = 0

  for (const path of paths.slice(0, MAX_UNTRACKED_FILES)) {
    const bytesToRead = Math.min(MAX_UNTRACKED_FILE_BYTES, MAX_UNTRACKED_TOTAL_BYTES - totalBytes)

    if (bytesToRead <= 0) {
      return MAX_UNTRACKED_TOTAL_LINES
    }

    let file

    try {
      file = await open(resolve(cwd, path), 'r')
      const content = Buffer.allocUnsafe(bytesToRead)
      const { bytesRead } = await file.read(content, 0, content.length, 0)
      totalBytes += bytesRead

      if (bytesRead === 0 || content.subarray(0, bytesRead).includes(0)) {
        continue
      }

      let lines = 0

      for (let idx = 0; idx < bytesRead; idx += 1) {
        if (content[idx] === 10) {
          lines += 1
        }
      }

      const truncated = bytesRead === bytesToRead
      const fileLines = truncated ? MAX_UNTRACKED_FILE_LINES : lines + (content[bytesRead - 1] === 10 ? 0 : 1)
      total = Math.min(MAX_UNTRACKED_TOTAL_LINES, total + fileLines)

      if (truncated || fileLines >= MAX_UNTRACKED_FILE_LINES || total >= MAX_UNTRACKED_TOTAL_LINES) {
        return MAX_UNTRACKED_TOTAL_LINES
      }
    } catch {
      // A file can disappear between git status and this probe.
    } finally {
      await file?.close().catch(() => undefined)
    }
  }

  return paths.length > MAX_UNTRACKED_FILES ? MAX_UNTRACKED_TOTAL_LINES : total
}

const parseStatus = (output: string) => {
  const records = output.split('\0')
  const untracked: string[] = []
  let files = 0

  for (let idx = 0; idx < records.length; idx += 1) {
    const record = records[idx]!
    const kind = record[0]

    if (kind === '1' || kind === 'u') {
      files += 1
    } else if (kind === '2') {
      files += 1
      idx += 1 // porcelain v2 emits the rename source as the next NUL field
    } else if (kind === '?') {
      files += 1
      untracked.push(record.slice(2))
    }
  }

  return { files, untracked }
}

export const resolveGitStatus = async (cwd: string): Promise<GitStatus> => {
  const inside = await runGit(cwd, ['rev-parse', '--is-inside-work-tree'])

  if (inside?.trim() !== 'true') {
    return EMPTY
  }

  const [branchOutput, commitOutput, statusOutput, stagedDiff, unstagedDiff] = await Promise.all([
    runGit(cwd, ['branch', '--show-current']),
    runGit(cwd, ['rev-parse', '--short', 'HEAD']),
    runGit(cwd, ['status', '--porcelain=v2', '-z', '--untracked-files=all']),
    runGit(cwd, ['diff', '--cached', '--numstat', '--']),
    runGit(cwd, ['diff', '--numstat', '--'])
  ])

  if (statusOutput === null) {
    return EMPTY
  }

  const { files, untracked } = parseStatus(statusOutput)
  const untrackedLines = await untrackedTextLines(cwd, untracked)

  return {
    branch: branchOutput?.trim() || commitOutput?.trim() || null,
    files,
    isRepo: true,
    lines: numstatLines(stagedDiff) + numstatLines(unstagedDiff) + untrackedLines
  }
}

const fetchStatus = (cwd: string): Promise<GitStatus> => {
  const pending = inflight.get(cwd)

  if (pending) {
    return pending
  }

  const promise = resolveGitStatus(cwd).finally(() => inflight.delete(cwd))
  inflight.set(cwd, promise)

  return promise
}

export function useGitStatus(cwd: string): GitStatus {
  const [status, setStatus] = useState<GitStatus>(() => cache.get(cwd)?.status ?? EMPTY)

  useEffect(() => {
    let cancelled = false

    const tick = async () => {
      const hit = cache.get(cwd)

      if (hit && Date.now() - hit.at < TTL_MS) {
        if (!cancelled) {
          setStatus(hit.status)
        }

        return
      }

      const next = await fetchStatus(cwd)
      cache.set(cwd, { at: Date.now(), status: next })

      if (!cancelled) {
        setStatus(next)
      }
    }

    void tick()
    const id = setInterval(() => void tick(), TTL_MS)

    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [cwd])

  return status
}
