import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { useStore } from '@nanostores/react'
import React from 'react'
import stripAnsi from 'strip-ansi'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createServerRequestHandler } from '../app/createServerRequestHandler.js'
import { $overlayState, patchOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { resetServerRequestsForTests, respondToServerRequest } from '../app/serverRequestStore.js'
import { ClarifyPrompt } from '../components/prompts.js'
import { DEFAULT_THEME } from '../theme.js'

const choices = ['audit', 'review_changes', 'continue', 'new_task', 'stop', 'Once', 'Always (YOLO; all commands auto-approved)']

const params = {
  choices,
  question: 'Choose next action',
  action_metadata: [
    { id: 'audit', label: 'Custom first', description: 'Read-only custom scenario' },
    { id: 'review_changes', label: 'Проверить изменения', description: 'Review evidence without product edits' }
  ],
  interaction: { remaining_seconds: 600, reminder_remaining_seconds: 300, reminded: false, show_countdown: true, phase: 'human' }
}

function Card() {
  const { clarify } = useStore($overlayState)

  if (!clarify) {
    return null
  }

  const answer = (value: string) => {
    respondToServerRequest(clarify.requestId, { answer: value })
    patchOverlayState({ clarify: null })
  }

  return <ClarifyPrompt onAnswer={answer} onCancel={() => answer('')} req={clarify} t={DEFAULT_THEME} />
}

function mount() {
  const frames: string[] = []
  const stdout = Object.assign(new PassThrough(), { columns: 160, rows: 30, isTTY: false })
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: () => {}, ref: () => {}, unref: () => {} })
  stdout.on('data', chunk => frames.push(chunk.toString()))

  const view = renderSync(<Card />, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stderr: new PassThrough() as unknown as NodeJS.WriteStream,
    patchConsole: false
  })

  return { stdin, text: () => stripAnsi(frames.join('')), clear: () => { frames.length = 0 }, close: () => { view.unmount(); view.cleanup() } }
}

beforeEach(() => { resetOverlayState(); resetServerRequestsForTests(); vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(0) })
afterEach(() => { vi.useRealTimers(); resetOverlayState(); resetServerRequestsForTests() })

it('projects host metadata to the real Ink card, refreshes replay timing without ringing, and never answers on expiry', async () => {
  const bell = vi.fn()
  const respond = vi.fn()
  const handler = createServerRequestHandler({ ringPromptBell: bell, setStatus: vi.fn() })
  const deliver = (p: typeof params, replayed = false) => handler({ id: 'srq-flow', method: 'clarify', params: p, respond, fail: vi.fn(), replayed })
  deliver(params)
  const view = mount()

  try {
    await vi.waitFor(() => expect(view.text()).toContain('600s remaining'))
    expect(view.text()).toContain('1. Custom first')
    expect(view.text()).toContain('2. Проверить изменения')
    expect(view.text()).toContain('Review evidence without product edits')
    expect(view.text()).not.toContain('Other (type your answer)')
    vi.setSystemTime(300000)
    await vi.waitFor(() => expect(view.text()).toContain('Reminder: a human decision is still pending.'), { timeout: 3000 })
    deliver({ ...params, interaction: { ...params.interaction, remaining_seconds: 300, reminder_remaining_seconds: 0, reminded: true } }, true)
    expect(bell).toHaveBeenCalledTimes(1)
    expect($overlayState.get().clarify?.interaction?.remaining_seconds).toBe(300)
    view.clear()
    vi.setSystemTime(600000)
    await vi.waitFor(() => expect(view.text()).toContain('expired; no action selected'), { timeout: 3000 })
    expect(respond).not.toHaveBeenCalled()
    stdinWrite(view.stdin, '2')
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(respond).not.toHaveBeenCalled()
    view.stdin.write('s')
    await vi.waitFor(() => expect(respond).toHaveBeenCalledWith({ answer: 'stop' }))
  } finally { view.close() }
})

it('checks the deadline at input time before the next countdown tick', async () => {
  const respond = vi.fn()
  createServerRequestHandler({ ringPromptBell: vi.fn(), setStatus: vi.fn() })({ id: 'srq-deadline', method: 'clarify', params, respond, fail: vi.fn() })
  const view = mount()

  try {
    await vi.waitFor(() => expect(view.text()).toContain('600s remaining'))
    vi.setSystemTime(600001)
    view.stdin.write('2')
    await new Promise(resolve => setImmediate(resolve))
    expect(respond).not.toHaveBeenCalled()
    view.stdin.write('s')
    await vi.waitFor(() => expect(respond).toHaveBeenCalledExactlyOnceWith({ answer: 'stop' }))
  } finally { view.close() }
})

function stdinWrite(stdin: PassThrough, text: string) { stdin.write(text) }

it.each([['2', 'review_changes'], ['1', 'audit'], ['o', 'Once'], ['a', 'Always (YOLO; all commands auto-approved)'], ['s', 'stop']])('dispatches %s as the host-owned stable answer %s immediately', async (key, answer) => {
  const respond = vi.fn()
  createServerRequestHandler({ ringPromptBell: vi.fn(), setStatus: vi.fn() })({ id: 'srq-answer', method: 'clarify', params, respond, fail: vi.fn() })
  const view = mount()

  try {
    await new Promise(resolve => setTimeout(resolve, 50))
    view.stdin.write(key)
    await vi.waitFor(() => expect(respond).toHaveBeenCalledExactlyOnceWith({ answer }))
  } finally { view.close() }
})

it('honors hidden countdown and ordinary batch reconnect locks', async () => {
  const handler = createServerRequestHandler({ ringPromptBell: vi.fn(), setStatus: vi.fn() })
  handler({ id: 'srq-hidden', method: 'clarify', params: { ...params, interaction: { ...params.interaction, show_countdown: false, phase: 'solver' } }, respond: vi.fn(), fail: vi.fn() })
  const view = mount()

  try {
    await vi.waitFor(() => expect(view.text()).toContain('Solver decision'))
    expect(view.text()).not.toContain('600s remaining')
  } finally { view.close() }

  handler({ id: 'srq-batch', method: 'clarify', params: { questions: [{ qid: 'a', question: 'First', choices: ['A'] }, { qid: 'b', question: 'Second' }], answers: { a: 'A' } }, respond: vi.fn(), fail: vi.fn(), replayed: true })
  expect($overlayState.get().clarify).toMatchObject({ requestId: 'srq-batch', answers: { a: 'A' }, questions: [{ qid: 'a' }, { qid: 'b' }] })
})
