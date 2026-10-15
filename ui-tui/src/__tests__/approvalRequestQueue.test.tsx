import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { useStore } from '@nanostores/react'
import React from 'react'
import stripAnsi from 'strip-ansi'
import { beforeEach, expect, it, vi } from 'vitest'

import { answerApprovalRequest } from '../app/approvalRequestQueue.js'
import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { createServerRequestHandler } from '../app/createServerRequestHandler.js'
import { $overlayState, getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { hasOpenServerRequest, resetServerRequestsForTests } from '../app/serverRequestStore.js'
import { getUiState, resetUiState } from '../app/uiStore.js'
import { PromptZone } from '../components/appOverlays.js'

const cancelHandler = () => createGatewayEventHandler({
  composer: { setInput: vi.fn() },
  gateway: { rpc: vi.fn(), gw: { request: vi.fn() } },
  session: {},
  submission: {},
  system: { sys: vi.fn() },
  transcript: {},
  voice: {}
} as any)

function Card() {
  const { approval } = useStore($overlayState)

  return <PromptZone
    cols={120}
    onApprovalChoice={choice => approval && answerApprovalRequest(approval.requestId, choice)}
    onClarifyAnswer={() => {}}
    onClarifyQuestionAnswer={() => {}}
    onSecretSubmit={() => {}}
    onSudoSubmit={() => {}}
    onVaultUnlockSubmit={() => {}}
  />
}

const deliver = (handler: ReturnType<typeof createServerRequestHandler>, id: string,
  params: Record<string, unknown> = { command: id, choices: ['once', 'deny'], allow_permanent: false }, replayed = false) => {
  const respond = vi.fn()
  expect(handler({ fail: vi.fn(), id, method: 'approval', params, replayed, respond })).toBe(true)

  return respond
}

beforeEach(() => {
  resetOverlayState()
  resetServerRequestsForTests()
  resetUiState()
})

it('keeps the oldest parallel approval visible without answering any request', () => {
  const handler = createServerRequestHandler({ ringPromptBell: vi.fn(), setStatus: vi.fn() })
  const ids = ['srq-first', 'srq-second', 'srq-third']
  const replies = ids.map(() => vi.fn())

  ids.forEach((id, index) => {
    expect(handler({
      fail: vi.fn(),
      id,
      method: 'approval',
      params: { command: `fixture ${id}`, choices: ['once', 'deny'], allow_permanent: false },
      respond: replies[index]!
    })).toBe(true)
  })

  expect(getOverlayState().approval?.requestId).toBe(ids[0])
  expect(ids.every(hasOpenServerRequest)).toBe(true)
  replies.forEach(reply => expect(reply).not.toHaveBeenCalled())

  const replay = deliver(handler, ids[0]!, undefined, true)
  const replayThird = deliver(handler, ids[2]!, undefined, true)
  expect(getOverlayState().approval?.requestId).toBe(ids[0])
  answerApprovalRequest(ids[1]!, 'once')
  expect(getOverlayState().approval?.requestId).toBe(ids[0])
  expect(replay).not.toHaveBeenCalled()

  answerApprovalRequest(ids[0]!, 'once')
  expect(replay).toHaveBeenCalledExactlyOnceWith({ choice: 'once' })
  expect(getOverlayState().approval?.requestId).toBe(ids[1])
  expect(getUiState().status).toBe('approval needed')
  answerApprovalRequest(ids[0]!, 'always')
  expect(replies[1]).not.toHaveBeenCalled()

  const onEvent = cancelHandler()
  onEvent({ type: 'request.cancel', payload: { id: ids[2]!, method: 'approval', reason: 'timeout' } })
  expect(getOverlayState().approval?.requestId).toBe(ids[1])
  expect(hasOpenServerRequest(ids[2]!)).toBe(false)
  expect(replayThird).not.toHaveBeenCalled()

  answerApprovalRequest(ids[1]!, 'deny')
  expect(replies[1]).toHaveBeenCalledExactlyOnceWith({ choice: 'deny' })
  expect(getOverlayState().approval).toBeNull()
  expect(ids.some(hasOpenServerRequest)).toBe(false)
  expect(replies[0]).not.toHaveBeenCalled()
  expect(replies[2]).not.toHaveBeenCalled()
})

it('advances the real Ink card on cancellation without inheriting another request selection', async () => {
  const bell = vi.fn()
  const handler = createServerRequestHandler({ ringPromptBell: bell, setStatus: vi.fn() })
  const first = deliver(handler, 'srq-wide', { command: 'first fixture', choices: ['once', 'session', 'always', 'deny'] })
  const second = deliver(handler, 'srq-protected', { command: 'protected fixture', choices: ['once', 'deny'], allow_permanent: false })
  const third = deliver(handler, 'srq-last')
  const onEvent = cancelHandler()
  const frames: string[] = []
  const stdout = Object.assign(new PassThrough(), { columns: 120, rows: 30, isTTY: false })
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: () => {}, ref: () => {}, unref: () => {} })
  stdout.on('data', chunk => frames.push(chunk.toString()))

  const view = renderSync(<Card />, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stderr: new PassThrough() as unknown as NodeJS.WriteStream,
    patchConsole: false
  })

  const text = () => stripAnsi(frames.join(''))

  try {
    await vi.waitFor(() => expect(text()).toContain('first fixture'))
    frames.length = 0
    stdin.write('\x1b[B')
    stdin.write('\x1b[B')
    await vi.waitFor(() => expect(text()).toMatch(/▸\s*3\. Always allow/))
    frames.length = 0
    onEvent({ type: 'request.cancel', payload: { id: 'srq-wide', method: 'approval', reason: 'timeout' } })
    await vi.waitFor(() => expect(text()).toContain('protected fixture'))
    expect(text()).not.toContain('Always allow')
    expect(text()).not.toContain('Allow this session')
    expect(text()).toMatch(/▸\s*1\. Allow once/)
    expect(first).not.toHaveBeenCalled()
    expect(second).not.toHaveBeenCalled()
    expect(third).not.toHaveBeenCalled()

    stdin.write('1')
    await vi.waitFor(() => expect(second).toHaveBeenCalledExactlyOnceWith({ choice: 'once' }))
    expect(getOverlayState().approval?.requestId).toBe('srq-last')
    onEvent({ type: 'request.cancel', payload: { id: 'srq-wide', method: 'approval', reason: 'timeout' } })
    expect(getOverlayState().approval?.requestId).toBe('srq-last')
    await vi.waitFor(() => expect(text()).toContain('srq-last'))
    stdin.write('\x1b')
    await vi.waitFor(() => expect(third).toHaveBeenCalledExactlyOnceWith({ choice: 'deny' }))
    expect(getOverlayState().approval).toBeNull()
    expect(bell).toHaveBeenCalledTimes(3)
  } finally {
    view.unmount()
    view.cleanup()
  }
})
