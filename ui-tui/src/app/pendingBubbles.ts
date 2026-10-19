import { toTranscriptMessages } from '../domain/messages.js'
import type { Msg, SessionInfo } from '../types.js'

export type PendingRow = NonNullable<SessionInfo['pending_submissions']>[number]

// Inputs whose transcript bubble this client already painted at submit time.
// A busy-time admission defers its bubble until the authority starts it, so
// the bubble lands after the in-flight assistant reply instead of inside it.
const shown = new Set<string>()
const notices = new Set<string>()

export const markBubbleShown = (inputId?: string) => { if (inputId) { shown.add(inputId) } }

export const resetBubbleLedger = () => { shown.clear(); notices.clear() }

/** Both claim frames and queued transitions can announce the same producer input.
 * A started resume already rendered its stored display row; a start-only attachment did not. */
export function showProducerNotice(
  card: { display_kind?: string | null; display_text?: string | null },
  admissionId: string | undefined,
  pending: PendingRow[] | undefined,
  appendMessage: (message: Msg) => void
): boolean {
  if (card.display_kind !== 'process_complete' && card.display_kind !== 'async_delegation_complete') { return false }

  const row = pending?.find(item => item.admission_id === admissionId)
  markBubbleShown(row?.input_id)

  if (admissionId && notices.has(admissionId)) { return true }

  if (admissionId) { notices.add(admissionId) }

  if (row?.status === 'started') { return true }

  for (const message of toTranscriptMessages([{ role: 'user', text: card.display_kind,
    display_kind: card.display_kind, display_metadata: { display_text: card.display_text } }])) {
    appendMessage(message)
  }

  return true
}

// Rows the authority just moved queued → started whose bubble nobody painted.
// Only a transition observed by THIS client counts: a resume snapshot's started
// row already lives in the replayed history.
export function newlyStartedRows(previous: PendingRow[] | undefined, incoming: PendingRow[] | undefined): PendingRow[] {
  if (!previous || !incoming) { return [] }
  const wasQueued = new Set(previous.filter(row => row.status === 'queued').map(row => row.admission_id))

  return incoming.filter(row => row.status === 'started' && wasQueued.has(row.admission_id) && !(row.input_id && shown.has(row.input_id)))
}

export const isServerQueued = (row: PendingRow) => row.status === 'queued' || row.status === 'unknown'
