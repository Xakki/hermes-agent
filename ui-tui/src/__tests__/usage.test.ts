import { describe, expect, it } from 'vitest'

import { sessionTokenLabel } from '../domain/usage.js'

describe('session token status label', () => {
  it('keeps cumulative input and output totals distinct', () => {
    expect(sessionTokenLabel({ calls: 2, input: 1234, output: 5678, total: 6912 })).toBe('in 1.2k / out 5.7k')
  })

  it('hides the label before the first model usage', () => {
    expect(sessionTokenLabel({ calls: 0, input: 0, output: 0, total: 0 })).toBe('')
  })
})
