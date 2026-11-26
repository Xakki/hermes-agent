import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { expect, it, vi } from 'vitest'

import { ActiveSessionSwitcher } from '../components/activeSessionSwitcher.js'
import { DEFAULT_THEME } from '../theme.js'

const current = {
  id: 'current',
  title: 'Current conversation',
  cwd: '/owner/pkg',
  git_repo_root: '/owner',
  git_branch: 'topic',
  started_at: Date.now() / 1000 - 90000,
  last_activity_at: Date.now() / 1000,
  message_count: 3
}

const setup = (info: object, columns = 110, supported = true, resumePicker = true) => {
  const request = vi.fn(
    async (method: string, params: { scope?: string; workspace?: string; workspace_cwd?: string }) => {
      if (method === 'session.info') {
        return info
      }

      return {
        scope: params.scope ?? 'live',
        sessions:
          params.scope === 'stored'
            ? params.workspace || params.workspace_cwd
              ? []
              : [{ id: 'other', title: 'Other conversation' }]
            : [current]
      }
    }
  )
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: vi.fn(), ref: vi.fn(), unref: vi.fn() })
  const stdout = Object.assign(new PassThrough(), { columns, rows: 32, isTTY: false })
  let output = ''
  stdout.on('data', chunk => {
    output = chunk.toString()
  })
  const element = (
    <ActiveSessionSwitcher
      currentSessionId="current"
      gw={{ isCanonical: supported, sessionListPicker: supported, sessionListWorkspaceCwd: supported, request } as any}
      maxWidth={columns - 2}
      onCancel={vi.fn()}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onNewPrompt={vi.fn()}
      onResume={vi.fn()}
      onSelect={vi.fn()}
      resumePicker={resumePicker}
      t={DEFAULT_THEME}
    />
  )
  const instance = renderSync(element, { stdin, stdout, stderr: new PassThrough(), patchConsole: false } as any)
  const frame = async (text: string) =>
    vi.waitFor(() => {
      instance.rerender(element)
      expect(output).toContain(text)
    })

  return { request, stdin, frame, output: () => output, unmount: instance.unmount }
}

it.each([
  [{ cwd: '/gateway/pkg', git_repo_root: '/gateway' }, { workspace: '/gateway' }, 110],
  [{ cwd: '/gateway/plain' }, { workspace_cwd: '/gateway/plain' }, 48],
  [{ cwd: '/gateway/pkg', git_repo_root: '/gateway' }, { workspace: '/gateway' }, 34]
])('renders metadata and toggles both feeds using the gateway workspace (%j)', async (info, selector, columns) => {
  const picker = setup(info, columns)
  try {
    await picker.frame('Other conversation')
    for (const value of ['/owner/pkg', '/owner', 'topic', 'yesterday', 'today', '3 msgs']) {
      expect(picker.output()).toContain(value)
    }
    expect(picker.output().match(/3 msgs/g)).toHaveLength(1)
    expect(picker.output().match(/yesterday/g)).toHaveLength(1)
    expect(picker.output().match(/today/g)).toHaveLength(1)
    expect(picker.output()).toContain('Msgs: —')
    picker.stdin.write('\u001b[B')
    picker.stdin.write('\u0014')
    await vi.waitFor(() => {
      expect(picker.request).toHaveBeenCalledWith('session.info', { session_id: 'current' })
      expect(picker.request).toHaveBeenCalledWith('session.list', { limit: 200, picker: true, ...selector })
      expect(picker.request).toHaveBeenCalledWith('session.list', {
        limit: 200,
        picker: true,
        scope: 'stored',
        ...selector
      })
    })
    await picker.frame('Workspace filter: on')
    expect(picker.output()).not.toContain('Other conversation')
    picker.request.mockClear()
    picker.stdin.write('\u0012')
    await vi.waitFor(() =>
      expect(picker.request).toHaveBeenCalledWith('session.list', {
        limit: 200,
        picker: true,
        scope: 'stored',
        ...selector
      })
    )
    picker.request.mockClear()
    picker.stdin.write('\u0014')
    await picker.frame('Other conversation')
    expect(picker.request).toHaveBeenCalledWith('session.list', { limit: 200, picker: true, scope: 'stored' })
  } finally {
    picker.unmount()
  }
})

it('never fetches an unrestricted feed while the workspace filter is unavailable', async () => {
  const picker = setup({})
  try {
    await picker.frame('Other conversation')
    picker.request.mockClear()
    picker.stdin.write('\u0014')
    await picker.frame('Workspace filter: unavailable')
    expect(picker.request.mock.calls.map(([name]) => name)).toEqual(['session.info'])
    expect(picker.output()).not.toContain('Other conversation')
    picker.stdin.write('\u0014')
    await picker.frame('Other conversation')
  } finally {
    picker.unmount()
  }
})

it('ignores a late unfiltered load after entering filtered mode', async () => {
  const picker = setup({ git_repo_root: '/gateway' })
  const late = Promise.withResolvers<{ sessions: { id: string; title: string }[] }>()
  const list = picker.request.getMockImplementation()!
  try {
    await picker.frame('Other conversation')
    picker.request.mockImplementation((method, params) =>
      method === 'session.list' && params.scope === 'stored' && !params.workspace
        ? (late.promise as any)
        : list(method, params)
    )
    picker.request.mockClear()
    picker.stdin.write('\u0012')
    await vi.waitFor(() =>
      expect(picker.request).toHaveBeenCalledWith('session.list', {
        limit: 200,
        picker: true,
        scope: 'stored'
      })
    )
    picker.request.mockClear()
    picker.stdin.write('\u0014')
    await picker.frame('Workspace filter: on')
    await vi.waitFor(() => expect(picker.output()).not.toContain('Other conversation'))
    late.resolve({ sessions: [{ id: 'late', title: 'Late unfiltered conversation' }] })
    await late.promise
    await picker.frame('Current conversation')
    expect(picker.output()).not.toContain('Late unfiltered conversation')
    await vi.waitFor(
      () => {
        const calls = picker.request.mock.calls.filter(([method]) => method === 'session.list')
        expect(calls.length).toBeGreaterThanOrEqual(3) // initial live + stored, then a live poll
        expect(calls.every(([, params]) => params.workspace === '/gateway')).toBe(true)
      },
      { timeout: 2500 }
    )
  } finally {
    late.resolve({ sessions: [] })
    picker.unmount()
  }
})

it('reports unavailable without sending any filter or list RPC to a legacy gateway', async () => {
  const picker = setup({}, 110, false)
  try {
    await picker.frame('Workspace filter: off')
    await picker.frame('Current conversation')
    picker.request.mockClear()
    picker.stdin.write('\u0014')
    await picker.frame('Workspace filter: unavailable')
    expect(picker.request).not.toHaveBeenCalled()
  } finally {
    picker.unmount()
  }
})

it('does not change the shared switcher outside /resume', async () => {
  const picker = setup({}, 110, true, false)
  try {
    await picker.frame('Other conversation')
    expect(picker.output()).not.toContain('/owner/pkg')
    expect(picker.output()).not.toContain('Ctrl+T')
    expect(picker.request).toHaveBeenCalledWith('session.list', { limit: 200, scope: 'stored' })
    picker.request.mockClear()
    picker.stdin.write('\u0014')
    await picker.frame('Other conversation')
    expect(picker.request).not.toHaveBeenCalled()
  } finally {
    picker.unmount()
  }
})
