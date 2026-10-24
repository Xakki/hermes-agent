import { EventEmitter } from 'node:events'
import { mkdtempSync, rmSync } from 'node:fs'
import type { Stats } from 'node:fs'
import { stat } from 'node:fs/promises'
import type * as FsPromises from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { loadSlashCatalog } from '../app/slashCatalog.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import { useComposerState } from '../app/useComposerState.js'
import { TextInput } from '../components/textInput.js'
import { imageToken } from '../domain/attachments.js'
import { readClipboardText } from '../lib/clipboard.js'

vi.mock('../lib/clipboard.js', () => ({
  readClipboardText: vi.fn(),
  writeClipboardText: vi.fn().mockResolvedValue(true),
  isUsableClipboardText: (value: unknown) => typeof value === 'string' && value.length > 0
}))

vi.mock('node:fs/promises', async importOriginal => ({
  ...(await importOriginal<typeof FsPromises>()),
  stat: vi.fn()
}))

class FixtureTty extends EventEmitter {
  columns = 100
  rows = 30
  isTTY = true
  isRaw = false
  private reads: string[] = []
  ref(): void {}
  unref(): void {}
  setEncoding(): this {
    return this
  }
  setRawMode(value: boolean): this {
    this.isRaw = value

    return this
  }
  read(): string | null {
    return this.reads.shift() ?? null
  }
  send(text: string): void {
    this.reads.push(text)
    this.emit('readable')
  }
  write(_chunk: string | Uint8Array, cb?: (error?: Error | null) => void): boolean {
    cb?.()

    return true
  }
}

let home: string
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'slash-paste-key-'))
  vi.stubEnv('HERMES_HOME', home)
  vi.stubEnv('TERM_PROGRAM', 'iTerm.app')
  vi.stubEnv('TMUX', '')
  vi.stubEnv('SSH_CONNECTION', '')
  vi.stubEnv('SSH_CLIENT', '')
  vi.stubEnv('SSH_TTY', '')
  resetUiState()
  patchUiState({ sid: 'fixture-owner', pasteCollapseChars: 0, pasteCollapseLines: 0 })
  vi.mocked(readClipboardText).mockReset()
  vi.mocked(stat)
    .mockReset()
    .mockRejectedValue(Object.assign(new Error('Fixture path is absent'), { code: 'ENOENT' }))
})

afterEach(() => {
  resetUiState()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
  rmSync(home, { recursive: true, force: true })
})

it.each([
  'plain text',
  '/help',
  '/plan explanatory text',
  '/help\nsecond line',
  '/help details.',
  '/plan inspect /tmp/example', // no-tmp: ok — text-only paste fixture; never used for filesystem I/O.
  '/plugin:skill explain x.y',
  '/help пояснение.'
])('Ctrl+V inserts %j while discovery RPCs remain pending', async text => {
  const stdin = new FixtureTty()
  const request = vi.fn((_method: string, _params?: unknown) => new Promise(() => {}))
  const receivedPaste = vi.fn()
  let composer!: ReturnType<typeof useComposerState>

  void loadSlashCatalog(request as any, 'fixture-owner', vi.fn())
  expect(request).toHaveBeenCalledWith('commands.catalog', { session_id: 'fixture-owner' })

  function Harness() {
    composer = useComposerState({ gw: { request }, submitRef: { current: vi.fn() }, sys: vi.fn() } as any)

    return (
      <TextInput
        onChange={composer.actions.setInput}
        onPaste={event => {
          receivedPaste(event)

          return composer.actions.handleTextPaste(event)
        }}
        value={composer.state.input}
      />
    )
  }

  const instance = renderSync(<Harness />, {
    stdin: stdin as any,
    stdout: new FixtureTty() as any,
    stderr: new FixtureTty() as any,
    patchConsole: false
  })

  try {
    await new Promise<void>(resolve => setImmediate(resolve))
    stdin.send('a')
    await vi.waitFor(() => expect(composer.state.input).toBe('a'))
    composer.actions.setInput('')
    await vi.waitFor(() => expect(composer.state.input).toBe(''))
    vi.mocked(readClipboardText).mockResolvedValueOnce(text)
    stdin.send('\x16')
    await vi.waitFor(() => expect(receivedPaste).toHaveBeenCalledTimes(1))
    expect(receivedPaste.mock.calls[0]![0]).toMatchObject({ hotkey: true, text: '' })
    await vi.waitFor(() => expect(composer.state.input).toBe(text))
    expect(request.mock.calls.some(([method]) => method === 'image.attach' || method === 'input.detect_drop')).toBe(
      false
    )
  } finally {
    instance.unmount()
    instance.cleanup()
  }
})

it.each([false, true, 'edit'])('preserves a real root filename with spaces; destination changes=%s', async changes => {
  const stdin = new FixtureTty()
  const text = '/Screenshot 2026.png'

  const request = vi.fn(async (method: string) =>
    method === 'image.attach' ? { name: 'Screenshot 2026.png', path: text } : {}
  )

  let finishStat!: (value: Stats) => void
  vi.mocked(stat).mockReturnValueOnce(
    new Promise<Stats>(resolve => {
      finishStat = resolve
    }) as ReturnType<typeof stat>
  )
  vi.mocked(readClipboardText).mockResolvedValueOnce(text)
  let composer!: ReturnType<typeof useComposerState>

  function Harness() {
    composer = useComposerState({ gw: { request }, submitRef: { current: vi.fn() }, sys: vi.fn() } as any)

    return (
      <TextInput
        onChange={composer.actions.setInput}
        onPaste={composer.actions.handleTextPaste}
        value={composer.state.input}
      />
    )
  }

  const instance = renderSync(<Harness />, {
    stdin: stdin as any,
    stdout: new FixtureTty() as any,
    stderr: new FixtureTty() as any,
    patchConsole: false
  })

  try {
    await new Promise<void>(resolve => setImmediate(resolve))
    stdin.send('a')
    await vi.waitFor(() => expect(composer.state.input).toBe('a'))
    composer.actions.setInput('')
    await vi.waitFor(() => expect(composer.state.input).toBe(''))
    stdin.send('\x16')
    await vi.waitFor(() => expect(stat).toHaveBeenCalledWith(text))

    if (changes === 'edit') {
      stdin.send('x')
      await vi.waitFor(() => expect(composer.state.input).toBe('x'))
    } else if (changes) {
      patchUiState({ sid: 'fixture-other-owner' })
    }

    finishStat({ isFile: () => true } as Stats)
    await new Promise<void>(resolve => setImmediate(resolve))

    if (changes) {
      expect(request.mock.calls.some(([method]) => method === 'image.attach')).toBe(false)
      expect(composer.state.input).toBe(changes === 'edit' ? 'x' : '')
    } else {
      await vi.waitFor(() => expect(composer.state.input).toBe(imageToken(1)))
      expect(request).toHaveBeenCalledWith('image.attach', { path: text, session_id: 'fixture-owner' })
    }
  } finally {
    instance.unmount()
    instance.cleanup()
  }
})
