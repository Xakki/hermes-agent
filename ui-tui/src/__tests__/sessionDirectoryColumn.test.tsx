import { PassThrough } from 'node:stream'
import { stripVTControlCharacters } from 'node:util'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { expect, it, vi } from 'vitest'

import { ActiveSessionSwitcher } from '../components/activeSessionSwitcher.js'
import { shortCwd } from '../domain/paths.js'
import { DEFAULT_THEME } from '../theme.js'

it.each([80, 120, 44])('renders recorded directories in aligned columns at %s columns', async columns => {
  const live = [
    { id: 'live', title: 'Live title', cwd: '/live/work', running: true },
    { id: 'missing', title: 'Missing title' }
  ]

  const stored = [
    { id: 'stored', title: 'Stored title', cwd: '/stored/work' },
    { id: 'empty', title: 'Empty title', cwd: '' },
    { id: 'posix', title: 'POSIX title', cwd: '/very/long/nested/path/to/a/project/workspace' },
    { id: 'windows', title: 'Windows title', cwd: 'C:\\Users\\developer\\projects\\nested\\workspace' }
  ]

  for (const canonical of [true, false]) {
    const request = vi.fn(async (method: string, params: { scope?: string }) => ({
      sessions: method === 'session.active_list' || (canonical && params.scope !== 'stored') ? live : stored
    }))

    const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: vi.fn(), ref: vi.fn(), unref: vi.fn() })
    const stdout = Object.assign(new PassThrough(), { columns, rows: 30, isTTY: false })
    let output = ''
    stdout.on('data', chunk => {
      output += chunk.toString()
    })

    const element = (
      <ActiveSessionSwitcher
        currentSessionId="live"
        gw={{ isCanonical: canonical, request } as any}
        maxWidth={columns - 6}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onNew={vi.fn()}
        onNewPrompt={vi.fn()}
        onResume={vi.fn()}
        onSelect={vi.fn()}
        t={DEFAULT_THEME}
      />
    )

    const instance = renderSync(element, {
      stdin: stdin as any,
      stdout: stdout as any,
      stderr: new PassThrough() as any,
      patchConsole: false
    })

    try {
      await vi.waitFor(() => {
        instance.rerender(React.cloneElement(element))
        expect(output).toContain('Stored title')
      })
      const lines = stripVTControlCharacters(output).split('\n')
      const row = (title: string) => lines.findLast(line => line.includes(title))!
      expect(output).toContain('Directory')
      const liveLine = row('Live title')
      const start = liveLine.indexOf('/live/work')
      expect(start).toBeGreaterThan(0)
      expect(row('Stored title').indexOf('/stored/work')).toBe(start)
      expect(row('Missing title').indexOf('—')).toBe(start)
      expect(row('Empty title').indexOf('—')).toBe(start)
      const cwdWidth = row('Stored title').indexOf('Stored title') - start - 1

      for (const item of stored.slice(2)) {
        expect(row(item.title).indexOf(shortCwd(item.cwd!, cwdWidth))).toBe(start)
        expect(row(item.title).length).toBeLessThanOrEqual(columns - 6)
      }

      expect(row('Live title').indexOf('Live title')).toBe(row('Stored title').indexOf('Stored title'))
      const newLine = lines.findLast(line => /\+\s+new/.test(line))!
      expect(newLine.slice(start, start + cwdWidth).trim(), newLine).toBe('—')
    } finally {
      instance.unmount()
    }
  }
})
