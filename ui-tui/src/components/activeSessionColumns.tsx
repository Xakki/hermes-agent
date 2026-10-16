import { Box, Text } from '@hermes/ink'

import { shortCwd } from '../domain/paths.js'
import type { Theme } from '../theme.js'

import { clampOverlayWidth } from './overlayPrimitives.js'

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
  const idWidth = compact ? 6 : 7
  const statusWidth = compact ? 0 : 10
  const modelWidth = compact ? 0 : 8
  const cwdWidth = Math.max(12, Math.min(28, Math.floor(width / 4)))

  return {
    cwdWidth,
    idWidth,
    modelColumn: metadataColumn(modelWidth),
    modelWidth,
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
    <Box flexShrink={0} paddingRight={1} width={width + 1}>
      <Text color={color ?? t.color.muted} wrap="truncate-end">
        {cwd?.trim() ? shortCwd(cwd, width) : '—'}
      </Text>
    </Box>
  )
}
