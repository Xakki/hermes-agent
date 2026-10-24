import { PassThrough } from 'node:stream'
import { stripVTControlCharacters } from 'node:util'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { sessionColumnLayout } from '../components/activeSessionColumns.js'
import { ActiveSessionSwitcher } from '../components/activeSessionSwitcher.js'
import { shortCwd } from '../domain/paths.js'
import { DEFAULT_THEME } from '../theme.js'

const originalColumns = Object.getOwnPropertyDescriptor(process.stdout, 'columns')

afterEach(() => {
  if (originalColumns) {
    Object.defineProperty(process.stdout, 'columns', originalColumns)
  } else {
    Reflect.deleteProperty(process.stdout, 'columns')
  }
})

it.each([80, 120, 44])('renders recorded directories in aligned columns at %s columns', async columns => {
  Object.defineProperty(process.stdout, 'columns', { configurable: true, value: columns, writable: true })

  const live = [
    {
      id: 'live-abcdefghij-12345',
      title: 'Live title',
      cwd: '/live/work',
      running: true,
      status: 'working',
      model: 'gpt-1234'
    },
    { id: 'missing', title: 'Missing title' }
  ]

  const stored = [
    { id: 'store-abcdefghij-abcde', title: 'Stored title', cwd: '/stored/work' },
    { id: 'empty', title: 'Empty title', cwd: '' },
    { id: 'posix', title: 'POSIX title', cwd: '/very/long/nested/path/to/a/project/workspace' },
    { id: 'windows', title: 'Windows title', cwd: 'C:\\Users\\developer\\projects\\nested\\workspace' },
    { id: 'short-key12', title: 'Short title', cwd: '/short/work' },
    { id: 'short-key123', title: 'Edge title', cwd: '/edge/work' }
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

    const onSelect = vi.fn()

    const element = (
      <ActiveSessionSwitcher
        currentSessionId="another-current-session"
        gw={{ isCanonical: canonical, request } as any}
        maxWidth={columns - 6}
        onCancel={vi.fn()}
        onClose={vi.fn()}
        onNew={vi.fn()}
        onNewPrompt={vi.fn()}
        onResume={vi.fn()}
        onSelect={onSelect}
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
      const { cwdWidth, modelWidth } = sessionColumnLayout({ columns }, columns - 6)
      const start = liveLine.indexOf(shortCwd('/live/work', cwdWidth))
      expect(start).toBeGreaterThan(0)
      expect(row('Stored title').indexOf(shortCwd('/stored/work', cwdWidth))).toBe(start)
      expect(row('Missing title').indexOf('—')).toBe(start)
      expect(row('Empty title').indexOf('—')).toBe(start)

      expect.soft(liveLine).toContain('live-…12345  ')
      expect.soft(row('Stored title')).toContain('store…abcde  ')
      expect.soft(row('Short title')).toContain('short-key12  ')
      expect.soft(row('Edge title')).toContain('short…ey123  ')

      if (modelWidth > 0) {
        expect.soft(liveLine).toContain('▶ working  ')
        expect.soft(liveLine).toContain('gpt-1234  ')
      }

      for (const item of stored.slice(2)) {
        expect(row(item.title).indexOf(shortCwd(item.cwd!, cwdWidth))).toBe(start)
        expect.soft(row(item.title).slice(start + cwdWidth, row(item.title).indexOf(item.title))).toBe('  ')
        expect(row(item.title).length).toBeLessThanOrEqual(columns - 6)
      }

      expect(row('Live title').indexOf('Live title')).toBe(row('Stored title').indexOf('Stored title'))
      expect(lines.findLast(line => line.includes('Directory'))!.indexOf('Directory')).toBe(start)

      stdin.write('\r')
      await vi.waitFor(() => expect(onSelect).toHaveBeenCalledWith(live[0].id))

      const newLine = lines.findLast(line => /\+\s+new/.test(line))!
      expect(newLine.slice(start, start + cwdWidth).trim(), newLine).toBe('—')
    } finally {
      instance.unmount()
    }
  }
})
