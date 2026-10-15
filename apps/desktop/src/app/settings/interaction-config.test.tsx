import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { en } from '@/i18n/en'

import { ConfigField } from './config-field'

vi.mock('@/i18n', () => ({ useI18n: () => ({ t: en }) }))

afterEach(cleanup)

it('edits real menu JSON drafts without persisting invalid input, and edits task-source order', () => {
  const saved = vi.fn()

  function Settings() {
    const [menu, setMenu] = useState<unknown>([{ id: 'review_changes', label: 'Проверить изменения', handler: 'prompt', prompt: 'Read changes only.' }])
    const [sources, setSources] = useState<unknown>(['todo', 'grooming', 'freeze'])

    return <>
      <ConfigField onChange={value => { setMenu(value); saved(value) }} schema={{ type: 'json' }} schemaKey="interaction.menu" value={menu} />
      <ConfigField onChange={setSources} schema={{ type: 'list' }} schemaKey="interaction.task_sources" value={sources} />
    </>
  }

  render(<Settings />)

  const editor = screen.getByLabelText('JSON configuration') as HTMLTextAreaElement

  expect(editor.value).toContain('Проверить изменения')
  fireEvent.change(editor, { target: { value: '[{' } })
  expect(editor.value).toBe('[{')
  expect(screen.getByRole('alert').textContent).toContain('last valid setting is unchanged')
  expect(saved).not.toHaveBeenCalled()

  const custom = [{ id: 'audit', label: 'Audit first', description: 'Evidence', enabled: true, handler: 'prompt', prompt: 'Read current changes only.' }]

  fireEvent.change(editor, { target: { value: JSON.stringify(custom) } })
  expect(saved).toHaveBeenLastCalledWith(custom)
  expect(screen.queryByRole('alert')).toBeNull()

  const sources = screen.getByPlaceholderText(en.settings.config.commaSeparated) as HTMLInputElement

  fireEvent.change(sources, { target: { value: 'freeze, todo, grooming' } })
  expect(sources.value).toBe('freeze, todo, grooming')
})
