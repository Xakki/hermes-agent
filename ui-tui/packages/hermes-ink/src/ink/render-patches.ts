import { CURSOR_HOME, cursorPosition, ERASE_SCREEN, ERASE_SCROLLBACK } from './termio/csi.js'

// Alt-screen content fills the TTY screen, so the renderer's anchor stays hidden.
export const ALT_SCREEN_ANCHOR_CURSOR = Object.freeze({ x: 0, y: 0, visible: false })

export const CURSOR_HOME_PATCH = Object.freeze({
  type: 'stdout' as const,
  content: CURSOR_HOME
})

export const ERASE_THEN_HOME_PATCH = Object.freeze({
  type: 'stdout' as const,
  content: ERASE_SCREEN + CURSOR_HOME
})

export const DEEP_ERASE_THEN_HOME_PATCH = Object.freeze({
  type: 'stdout' as const,
  content: ERASE_SCREEN + ERASE_SCROLLBACK + CURSOR_HOME
})

// Cached per Ink instance and invalidated on resize.
export function makeAltScreenParkPatch(terminalRows: number) {
  return Object.freeze({
    type: 'stdout' as const,
    content: cursorPosition(terminalRows, 1)
  })
}
