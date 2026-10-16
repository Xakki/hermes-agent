import { Box, Text } from '@hermes/ink'

import { shortCwd } from '../domain/paths.js'
import type { Theme } from '../theme.js'

import { clampOverlayWidth } from './overlayPrimitives.js'

export const SESSION_COLUMN_GAP = 2
const SESSION_ID_WIDTH = 11

export const shortSessionId = (id: string) =>
  id.length > SESSION_ID_WIDTH ? `${id.slice(0, 5)}…${id.slice(-5)}` : id

const metadataColumn = (width: number) => ({
  display: width ? ('flex' as const) : ('none' as const),
  width
})

/** Keep titles useful by compacting secondary metadata before the directory. */
export const sessionColumnLayout = (stdout: { columns?: number } | undefined, maxWidth?: number) => {
  const columns = stdout?.columns ?? 80
  const preferredWidth = Math.max(64, Math.min(128, columns - 6))
  const width = clampOverlayWidth(preferredWidth, maxWidth ?? Math.max(1, columns - 6))
  const compact = width < 60
  const idWidth = SESSION_ID_WIDTH
  const statusWidth = compact ? 0 : 10
  const modelWidth = width < 90 ? 0 : 8

  const prefixWidth =
    5 +
    SESSION_COLUMN_GAP +
    [idWidth, statusWidth, modelWidth].reduce((total, column) => total + (column ? column + SESSION_COLUMN_GAP : 0), 0)

  const cwdWidth = Math.max(
    1,
    Math.min(28, Math.max(12, Math.floor(width / 5)), width - prefixWidth - SESSION_COLUMN_GAP - 14)
  )

  return {
    cwdWidth,
    idWidth,
    modelColumn: metadataColumn(modelWidth),
    modelWidth,
    prefixWidth,
    statusColumn: metadataColumn(statusWidth),
    statusWidth,
    width
  }
}

interface SessionDirectoryColumnProps {
  color?: string
  cwd?: null | string
  t: Theme
  width: number
}

export function SessionDirectoryColumn({ color, cwd, t, width }: SessionDirectoryColumnProps) {
  return (
    <Box flexShrink={0} paddingRight={SESSION_COLUMN_GAP} width={width + SESSION_COLUMN_GAP}>
      <Text color={color ?? t.color.muted} wrap="truncate-end">
        {cwd?.trim() ? shortCwd(cwd, width) : '—'}
      </Text>
    </Box>
  )
}
