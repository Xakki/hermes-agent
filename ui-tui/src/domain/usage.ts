import type { Usage } from '@hermes/shared/gateway-events'
import { compactNumber } from '@hermes/shared/format'

export const ZERO: Usage = { calls: 0, input: 0, output: 0, total: 0 }

export const sessionTokenLabel = (usage: Usage): string => {
  const input = usage.input ?? 0
  const output = usage.output ?? 0

  return input > 0 || output > 0 ? `in ${compactNumber(input)} / out ${compactNumber(output)}` : ''
}
