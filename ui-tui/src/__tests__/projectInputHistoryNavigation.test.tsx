import { mkdtempSync } from 'node:fs'
import { resolve } from 'node:path'
import { PassThrough } from 'node:stream'

import type * as Ink from '@hermes/ink'
import React from 'react'
import { expect, it, vi } from 'vitest'

import type { InputHandlerContext, UseComposerStateResult } from '../app/interfaces.js'
import type { GatewayClient } from '../gatewayClient.js'
import type { SessionInfo } from '../types.js'

type InputHandler = Parameters<typeof Ink.useInput>[0]
const inputHarness = vi.hoisted(() => ({ handler: undefined as InputHandler | undefined }))

vi.mock('@hermes/ink', async importOriginal => ({
  ...(await importOriginal<typeof Ink>()),
  useInput: (handler: InputHandler) => { inputHarness.handler = handler }
}))

it('switches mounted Up/Down recall A → B → A while retaining same-project session history', async () => {
  vi.stubEnv('HERMES_HOME', mkdtempSync(resolve('../tmp/project-input-history/navigation-')))
  const keyA = 'a'.repeat(64)
  const keyB = 'b'.repeat(64)
  const history = await import('../lib/history.js')
  history.append('project A older', keyA)
  history.append('project A latest', keyA)
  history.append('project B only', keyB)
  const { renderSync } = await import('@hermes/ink')
  const { useComposerState } = await import('../app/useComposerState.js')
  const { useInputHandlers } = await import('../app/useInputHandlers.js')
  const { setInputSelection } = await import('../app/inputSelectionStore.js')
  const { resetOverlayState } = await import('../app/overlayStore.js')
  const { patchUiState, resetUiState } = await import('../app/uiStore.js')
  resetOverlayState()
  resetUiState()

  const select = (sid: string, key: string | null) => patchUiState({
    sid, info: { input_history_key: key } as SessionInfo
  })

  select('a-1', keyA)

  const gw = { isCanonical: true, request: vi.fn(async () => ({})) } as unknown as GatewayClient
  const stdout = Object.assign(new PassThrough(), { columns: 100, rows: 30, isTTY: false })
  const submitRef = { current: vi.fn() }
  let composer: UseComposerStateResult

  function Harness() {
    composer = useComposerState({ gw, submitRef, sys: vi.fn() })
    useInputHandlers({
      composer, actions: { sys: vi.fn() }, gateway: { gw },
      terminal: { stdout, hasSelection: false }, voice: { enabled: false }, wheelStep: 1
    } as InputHandlerContext)

    return null
  }

  const instance = renderSync(<Harness />, {
    stdin: Object.assign(new PassThrough(), { isTTY: false }) as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream,
    stderr: new PassThrough() as NodeJS.WriteStream, patchConsole: false
  })

  const expectInput = async (expected: string) => vi.waitFor(() => {
    instance.rerender(<Harness />)
    expect(composer.state.input).toBe(expected)
  })

  const press = async (direction: 'up' | 'down', expected: string) => {
    setInputSelection({
      start: 0, end: 0, value: composer.state.input,
      clear: vi.fn(), collapseToEnd: vi.fn(), copy: vi.fn(), cut: vi.fn()
    })
    inputHarness.handler!('', { upArrow: direction === 'up', downArrow: direction === 'down' } as Parameters<InputHandler>[1], {} as Parameters<InputHandler>[2])
    await expectInput(expected)
  }

  try {
    composer!.actions.setInput('draft A')
    await expectInput('draft A')
    await press('up', 'project A latest')
    select('a-2', keyA)
    await press('up', 'project A older')
    await press('down', 'project A latest')
    await press('down', 'draft A')
    await press('up', 'project A latest')

    select('b-1', keyB)
    await vi.waitFor(() => {
      instance.rerender(<Harness />)
      expect(composer!.state.historyIdx).toBeNull()
      expect(composer!.refs.historyRef.current).toEqual(['project B only'])
      expect(composer!.refs.historyDraftRef.current).toBe('')
    })
    composer!.actions.setInput('draft B')
    await expectInput('draft B')
    await press('up', 'project B only')
    await press('up', 'project B only')
    await press('down', 'draft B')
    composer!.actions.pushHistory('project B second')

    select('a-3', keyA)
    await vi.waitFor(() => {
      instance.rerender(<Harness />)
      expect(composer!.refs.historyRef.current).toEqual(['project A older', 'project A latest'])
    })
    composer!.actions.setInput('new draft A')
    await expectInput('new draft A')
    await press('up', 'project A latest')
    await press('down', 'new draft A')

    select('unknown', null)
    await vi.waitFor(() => {
      instance.rerender(<Harness />)
      expect(composer!.refs.historyRef.current).toEqual([])
      expect(composer!.state.historyIdx).toBeNull()
    })
    composer!.actions.pushHistory('unknown project input')
    expect(history.load(keyA)).toEqual(['project A older', 'project A latest'])
    expect(history.load(keyB)).toEqual(['project B only', 'project B second'])
  } finally {
    instance.unmount()
    instance.cleanup()
    setInputSelection(null)
    resetOverlayState()
    resetUiState()
    vi.unstubAllEnvs()
    inputHarness.handler = undefined
  }
})
