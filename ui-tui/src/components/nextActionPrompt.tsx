import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useState } from 'react'

import { closeNextAction, type NextActionCard, secondsRemaining } from '../app/nextAction.js'

export function NextActionPrompt({ card }: { card: NextActionCard }) {
  const [seconds, setSeconds] = useState(() => secondsRemaining(card.deadline_ms))
  const [selected, setSelected] = useState(-1)

  useEffect(() => {
    const deadline = card.deadline_ms
    const update = () => setSeconds(secondsRemaining(deadline))
    const interval = card.show_countdown ? setInterval(update, 1000) : undefined
    const timeout = setTimeout(() => closeNextAction(card.id), Math.max(0, deadline - Date.now()))

    return () => {
      clearInterval(interval)
      clearTimeout(timeout)
    }
  }, [card])

  useInput((_, key) => {
    if (!secondsRemaining(card.deadline_ms)) {
      closeNextAction(card.id)
      return
    }

    if (key.escape) {
      card.select('close')
    } else if (key.upArrow) {
      setSelected(index => index <= 0 ? card.actions.length - 1 : index - 1)
    } else if (key.downArrow) {
      setSelected(index => (index + 1) % card.actions.length)
    } else if (key.return && selected >= 0) {
      const action = card.actions[selected]
      if (action) {
        card.select(action.id)
      }
    }
  })

  if (!seconds) {
    return null
  }

  return (
    <Box flexDirection="column">
      <Text bold>{card.show_countdown ? `Next action · ${seconds}s remaining` : 'Next action'}</Text>
      {card.actions.map((action, index) => (
        <Text key={action.id} bold={index === selected}>
          {index === selected ? '▸ ' : '  '}{action.label}{action.description ? ` — ${action.description}` : ''}
        </Text>
      ))}
      <Text dimColor>↑/↓ move · Enter select · Esc close</Text>
    </Box>
  )
}
