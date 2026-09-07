import type { Usage } from '../types.js'
import { fmtK } from '../lib/text.js'

export const ZERO: Usage = { calls: 0, input: 0, output: 0, total: 0 }

export const sessionTokenLabel = (usage: Usage): string =>
  usage.input > 0 || usage.output > 0 ? `in ${fmtK(usage.input)} / out ${fmtK(usage.output)}` : ''
