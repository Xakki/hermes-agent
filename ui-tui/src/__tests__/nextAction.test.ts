import { afterEach, expect, it, vi } from 'vitest'

import { secondsRemaining, showNextAction } from '../app/nextAction.js'
import { $overlayState, resetOverlayState } from '../app/overlayStore.js'

const offer = {
  actions: [{ description: '', id: 'continue', label: 'Continue' }],
  deadline_ms: 5000,
  id: 'offer',
  show_countdown: false
}

afterEach(() => {
  resetOverlayState()
  vi.useRealTimers()
})

it('counts down against the same deadline and never submits at zero, even with hidden digits', () => {
  vi.useFakeTimers()
  vi.setSystemTime(3000)
  const rpc = vi.fn(async () => null)
  showNextAction('session', offer, rpc, vi.fn())
  expect(secondsRemaining(offer.deadline_ms)).toBe(2)
  vi.advanceTimersByTime(1000)
  expect(secondsRemaining(offer.deadline_ms)).toBe(1)
  const card = $overlayState.get().nextAction
  expect(card?.show_countdown).toBe(false)
  vi.advanceTimersByTime(1000)
  card?.select('continue')
  expect($overlayState.get().nextAction).toBeNull()
  expect(rpc).not.toHaveBeenCalled()
})

it('submits only the selected offered ID and ignores repeat input', () => {
  vi.useFakeTimers()
  vi.setSystemTime(3000)
  const rpc = vi.fn(async () => null)
  showNextAction('session', offer, rpc, vi.fn())
  const card = $overlayState.get().nextAction
  expect(card?.actions).toEqual(offer.actions)
  card?.select('/diff')
  expect(rpc).not.toHaveBeenCalled()
  card?.select('continue')
  card?.select('continue')
  expect(rpc).toHaveBeenCalledExactlyOnceWith('interaction.next_action.respond', {
    action_id: 'continue', offer_id: 'offer', session_id: 'session'
  })
})
