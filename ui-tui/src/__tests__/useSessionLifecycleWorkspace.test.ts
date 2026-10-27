import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React, { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayRpc } from '../app/interfaces.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import { useSessionLifecycle, type UseSessionLifecycleOptions } from '../app/useSessionLifecycle.js'
import type * as Env from '../config/env.js'
import type { SessionInfo } from '../types.js'

const startup = vi.hoisted(() => ({ cwd: '' }))

vi.mock('../config/env.js', async importOriginal => ({
  ...(await importOriginal<typeof Env>()),
  get STARTUP_WORKSPACE_CWD() {
    return startup.cwd
  }
}))

const disposers: (() => void)[] = []
const infoFor = (cwd: string): SessionInfo => ({ cwd, model: 'test', skills: {}, tools: {} })

function mountLifecycle(
  rpc: GatewayRpc,
  request: (method: string, params?: Record<string, unknown>) => Promise<unknown> = vi.fn(async () => null)
) {
  let api: null | ReturnType<typeof useSessionLifecycle> = null

  function Probe() {
    const lifecycle = useSessionLifecycle({
      colsRef: { current: 80 },
      composerActions: { setComposerTokens: vi.fn() } as UseSessionLifecycleOptions['composerActions'],
      gw: { request } as unknown as UseSessionLifecycleOptions['gw'],
      panel: vi.fn(),
      rpc,
      scrollRef: { current: null },
      setHistoryItems: vi.fn(),
      setLastUserMsg: vi.fn(),
      setSessionStartedAt: vi.fn(),
      setStickyPrompt: vi.fn(),
      setVoiceProcessing: vi.fn(),
      setVoiceRecording: vi.fn(),
      sys: vi.fn()
    })

    useEffect(() => {
      api = lifecycle
    })

    return null
  }

  const stream = () => Object.assign(new PassThrough(), { columns: 80, isTTY: false, rows: 24 })

  const instance = renderSync(React.createElement(Probe), {
    patchConsole: false,
    stderr: stream() as unknown as NodeJS.WriteStream,
    stdin: stream() as unknown as NodeJS.ReadStream,
    stdout: stream() as unknown as NodeJS.WriteStream
  })

  disposers.push(() => {
    instance.unmount()
    instance.cleanup()
  })

  return () => {
    if (!api) {
      throw new Error('Lifecycle hook has not mounted')
    }

    return api
  }
}

function createRpc(onClose = () => {}) {
  return vi.fn(async (method: string, _params?: Record<string, unknown>) => {
    if (method === 'setup.status') {
      return { provider_configured: true }
    }

    if (method === 'session.close') {
      onClose()

      return {}
    }

    if (method === 'session.create') {
      return {
        info: infoFor('/server-resolved'),
        session_id: 'new-runtime',
        stored_session_id: 'new-durable'
      }
    }

    throw new Error(`Unexpected RPC: ${method}`)
  })
}

beforeEach(() => {
  startup.cwd = ''
  vi.stubEnv('HERMES_TUI_ACTIVE_SESSION_FILE', '')
  resetUiState()
  resetTurnState()
  turnController.fullReset()
})

afterEach(() => {
  while (disposers.length) {
    disposers.pop()!()
  }

  vi.unstubAllEnvs()
  resetUiState()
  resetTurnState()
  turnController.fullReset()
})

describe('useSessionLifecycle workspace inheritance', () => {
  it('snapshots the workspace before closing the current session', async () => {
    patchUiState({ info: infoFor('/workspace/current'), sid: 'old-runtime' })
    const rpc = createRpc(() => patchUiState({ info: null, sid: null }))
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newSession()

    expect(rpc.mock.calls.map(([method]) => method)).toEqual(['setup.status', 'session.close', 'session.create'])
    expect(rpc).toHaveBeenCalledWith('session.close', { session_id: 'old-runtime' })
    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/workspace/current' })
    expect(getUiState().sid).toBe('new-runtime')
    expect(getUiState().storedSid).toBe('new-durable')
    expect(getUiState().info?.cwd).toBe('/server-resolved')
  })

  it('prefers the current workspace over the dashboard startup workspace', async () => {
    startup.cwd = '/dashboard/startup'
    patchUiState({ info: infoFor('/dashboard/moved'), sid: 'old-runtime' })
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newSession()

    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/dashboard/moved' })
  })

  it('reads the latest workspace when the operation starts', async () => {
    patchUiState({ info: infoFor('/workspace/original'), sid: 'old-runtime' })
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)
    patchUiState({ info: infoFor('/workspace/moved') })

    await api().newSession()

    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/workspace/moved' })
  })

  it('inherits the workspace from an activated session', async () => {
    const request = vi.fn(async () => ({
      info: infoFor('/workspace/resumed'),
      messages: [],
      running: false,
      session_id: 'resumed-runtime',
      session_key: 'resumed-durable',
      status: 'idle'
    }))

    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc, request)
    await vi.waitFor(api)
    api().activateLiveSession('resumed-durable')
    await vi.waitFor(() => expect(getUiState().sid).toBe('resumed-runtime'))

    await api().newSession()

    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/workspace/resumed' })
  })

  it('preserves the workspace without closing the old live session', async () => {
    patchUiState({ info: infoFor('/workspace/current'), sid: 'old-runtime' })
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newLiveSession()

    expect(rpc.mock.calls.map(([method]) => method)).toEqual(['setup.status', 'session.create'])
    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/workspace/current' })
  })

  it.each(['~/remote/project', '/workspace/project with spaces', 'C:\\Users\\developer\\project'])(
    'passes the workspace literal unchanged: %s',
    async cwd => {
      patchUiState({ info: infoFor(cwd), sid: 'old-runtime' })
      const rpc = createRpc()
      const api = mountLifecycle(rpc as unknown as GatewayRpc)
      await vi.waitFor(api)

      await api().newSession()

      expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd })
    }
  )

  it('keeps the startup fallback when no current workspace is known', async () => {
    startup.cwd = '/dashboard/startup'
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newSession()

    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80, cwd: '/dashboard/startup' })
  })

  it('leaves cwd omitted when neither workspace source exists', async () => {
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newSession()

    expect(rpc).toHaveBeenCalledWith('session.create', { cols: 80 })
  })

  it('does not close the session when provider setup is required', async () => {
    patchUiState({ info: infoFor('/workspace/current'), sid: 'old-runtime' })
    const rpc = vi.fn(async () => ({ provider_configured: false }))
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    expect(await api().newSession()).toBeNull()

    expect(rpc.mock.calls).toEqual([['setup.status', {}]])
    expect(getUiState().sid).toBe('old-runtime')
    expect(getUiState().info?.cwd).toBe('/workspace/current')
  })

  it('does not mutate OS or profile home variables', async () => {
    const home = { HOME: process.env.HOME, HERMES_HOME: process.env.HERMES_HOME }
    patchUiState({ info: infoFor('/workspace/current'), sid: 'old-runtime' })
    const rpc = createRpc()
    const api = mountLifecycle(rpc as unknown as GatewayRpc)
    await vi.waitFor(api)

    await api().newSession()

    expect({ HOME: process.env.HOME, HERMES_HOME: process.env.HERMES_HOME }).toEqual(home)
  })
})
