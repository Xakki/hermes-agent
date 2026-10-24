import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useState } from 'react'

import type { Theme } from '../theme.js'
import type { ClarifyReq } from '../types.js'

import { chipRowProps } from './overlayPrimitives.js'
import { TextInput } from './textInput.js'

interface InteractionPromptProps {
  req: ClarifyReq
  t: Theme
  onAnswer: (answer: string) => void
  onCancel: () => void
}

function interactionInputLocked(typing: boolean, timing: ClarifyReq['interaction'], remaining: number, receivedAt: number | undefined, now: number) {
  return typing || Boolean(timing && (remaining === 0 || now >= (receivedAt ?? now) + timing.remaining_seconds * 1000))
}

function selectedInteractionRow(ch: string, enter: boolean, selected: number, rows: number) {
  const number = parseInt(ch, 10)

  return enter ? selected : number >= 1 && number <= rows ? number - 1 : -1
}

/** Host-owned native interaction; this renderer never chooses on deadline expiry. */
export function InteractionPrompt({ req, t, onAnswer, onCancel }: InteractionPromptProps) {
  const [sel, setSel] = useState(0)
  const [custom, setCustom] = useState('')
  const [typing, setTyping] = useState(false)
  const [now, setNow] = useState(Date.now())
  const timing = req.interaction
  const choices = req.choices ?? []
  const elapsed = Math.max(0, (now - (req.receivedAt ?? now)) / 1000)
  const remaining = Math.max(0, Math.ceil((timing?.remaining_seconds ?? 0) - elapsed))
  const reminded = timing && (timing.reminded || elapsed >= timing.reminder_remaining_seconds)

  useEffect(() => {
    if (!timing) {return}
    const timer = setInterval(() => setNow(Date.now()), 1000)

    return () => clearInterval(timer)
  }, [timing])

  useInput((ch, key) => {
    if (key.escape) {return onCancel()}

    if (timing && (ch.toLowerCase() === 's' || (key.ctrl && ch === 'c'))) {return onAnswer('stop')}

    if (interactionInputLocked(typing, timing, remaining, req.receivedAt, Date.now())) {return}

    if (timing && (ch.toLowerCase() === 'o' || ch.toLowerCase() === 'a')) {
      const value = ch.toLowerCase() === 'o' ? 'Once' : 'Always (YOLO; all commands auto-approved)'

      if (choices.includes(value)) {onAnswer(value)}

      return
    }

    const rows = timing ? choices : [...choices, 'Other (type your answer)']

    if (key.upArrow) {setSel(s => Math.max(0, s - 1))}

    if (key.downArrow) {setSel(s => Math.min(rows.length - 1, s + 1))}
    const index = selectedInteractionRow(ch, key.return, sel, rows.length)

    if (index < 0) {return}

    if (index === choices.length) {setTyping(true)}
    else if (choices[index]) {onAnswer(choices[index]!)}
  })

  return <Box flexDirection="column">
    <Text bold color={t.color.accent}>{req.question}</Text>
    {timing ? <>
      <Text color={t.color.muted}>
        {timing.phase === 'human' ? 'Human decision' : 'Solver decision'}
        {timing.show_countdown ? ` · ${remaining}s remaining` : ''}
        {remaining === 0 ? ' · expired; no action selected' : ''}
      </Text>
      {reminded && remaining > 0 && timing.phase === 'human' ? <Text color={t.color.warn}>Reminder: a human decision is still pending.</Text> : null}
      <Text color={t.color.warn}>Once: one solver decision. Always: native YOLO / AUTO, including command auto-approval. Stop: cancel now.</Text>
    </> : null}
    {typing || choices.length === 0
      ? <TextInput color={t.color.text} onChange={setCustom} onSubmit={onAnswer} value={custom} />
      : (timing ? choices : [...choices, 'Other (type your answer)']).map((choice, index) => <Text key={choice}>
        <Text color={t.color.muted} {...chipRowProps(t, sel === index)}>{sel === index ? '▸ ' : '  '}{index + 1}. {req.actionMetadata?.find(row => row.id === choice)?.label ?? choice}</Text>
        {req.actionMetadata?.find(row => row.id === choice)?.description ? <Text color={t.color.muted}> · {req.actionMetadata.find(row => row.id === choice)!.description}</Text> : null}
      </Text>)}
    <Text color={t.color.muted}>↑/↓ select · Enter confirm · Esc cancel{timing ? ' · O Once · A Always · S Stop' : ''}</Text>
  </Box>
}
