import { Box, Text, useInput, useStdout } from '@hermes/ink'
import type { SessionListResult, SessionListRow } from '@hermes/shared/gateway-events'
import type { SessionActiveItem, SessionActiveListResponse, SessionCloseResponse } from '@hermes/shared/gateway-types'
import { sessionScopedModelArg } from '@hermes/shared/slash-input'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import { useSessionDelete } from '../hooks/useSessionDelete.js'
import { messages } from '../i18n/runtime.js'
import { useT } from '../i18n/useT.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { ModelPicker } from './modelPicker.js'
import { windowOffset } from './overlayControls.js'
import { clampOverlayWidth, listRowStyle } from './overlayPrimitives.js'
import { TextInput } from './textInput.js'

const VISIBLE = 12
const RESUME_TOGGLE_DEBOUNCE_MS = 80
const MIN_WIDTH = 64
const MAX_WIDTH = 128
const TITLE_MAX = 64

const STATUS_GLYPH: Record<string, string> = {
  idle: '✓',
  starting: '…',
  waiting: '?',
  working: '▶'
}

/** Human label for a live-session status value; unknown statuses render as-is.
 *  Resolved at call time so it follows the active language. */
export const sessionStatusLabel = (status: string): string => {
  const labels: Record<string, string | undefined> = messages().pickers.session.status

  return labels[status] ?? status
}

const CTRL_OFFSET = 96

const shortModel = (model = '') => model.replace(/^.*\//, '') || messages().pickers.session.modelUnknown
const ctrlChar = (letter: string) => String.fromCharCode(letter.charCodeAt(0) - CTRL_OFFSET)

export const fixedSessionColumnStyle = () => ({ flexShrink: 0 })

export const activeSessionCountLabel = (count: number) =>
  count === 1 ? messages().pickers.session.liveSessionsOne(1) : messages().pickers.session.liveSessionsOther(count)

export const sessionsCountLabel = (liveCount: number, resumableCount: number) =>
  messages().pickers.session.counts(liveCount, resumableCount)

export type SessionRowKind = 'history' | 'live' | 'new'

/**
 * Map a flat row index into the merged Sessions list to its kind. Rows are
 * ordered [new][live…][history…] — the "+ new" row is pinned first so it is
 * always visible no matter how long the resumable history grows.
 */
export const sessionRowKindAt = (index: number, liveCount: number): SessionRowKind => {
  if (index <= 0) {
    return 'new'
  }

  return index - 1 < liveCount ? 'live' : 'history'
}

export const relativeSessionAge = (ts?: number) => {
  if (!ts) {
    return ''
  }

  const days = (Date.now() / 1000 - ts) / 86400

  const age = messages().pickers.session.age

  if (days < 1) {
    return age.today
  }

  if (days < 2) {
    return age.yesterday
  }

  return age.daysAgo(Math.floor(days))
}

/** Drop already-live sessions from the resumable history list (dedupe by id). */
export const resumableHistory = (history: readonly SessionListRow[], live: readonly SessionActiveItem[]) => {
  const liveIds = new Set(live.map(s => s.id))

  return history.filter(h => !liveIds.has(h.id))
}

// Hint segment tables are functions (not module constants) so their text
// fragments resolve against the active language at render time.
export const resumeRowContextHintSegments = (): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'label', text: h.resumableLabel },
    { role: 'text', text: ' ' },
    { role: 'hotkey', text: 'Enter' },
    { role: 'text', text: h.resume },
    { role: 'hotkey', text: 'd' },
    { role: 'text', text: h.delete }
  ]
}

export type OrchestratorHintRole = 'hotkey' | 'label' | 'text'

export interface OrchestratorHintSegment {
  role: OrchestratorHintRole
  text: string
}

export const orchestratorContextHintSegments = (newSelected: boolean): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return newSelected
    ? [
        { role: 'label', text: h.newRowLabel },
        { role: 'text', text: h.typePrompt },
        { role: 'hotkey', text: 'Enter' },
        { role: 'text', text: h.start },
        { role: 'hotkey', text: 'Tab' },
        { role: 'text', text: h.model }
      ]
    : [
        { role: 'label', text: h.sessionRowLabel },
        { role: 'text', text: ' ' },
        { role: 'hotkey', text: 'Enter' },
        { role: 'text', text: h.switch },
        { role: 'hotkey', text: 'Ctrl+D' },
        { role: 'text', text: h.close }
      ]
}

export const orchestratorGlobalHotkeyHintSegments = (): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'hotkey', text: '↑↓' },
    { role: 'text', text: h.move },
    { role: 'hotkey', text: 'Ctrl+N' },
    { role: 'text', text: h.new },
    { role: 'hotkey', text: 'Ctrl+R' },
    { role: 'text', text: h.refresh },
    { role: 'hotkey', text: 'Esc' },
    { role: 'text', text: h.close }
  ]
}

const hintText = (segments: readonly OrchestratorHintSegment[]) => segments.map(segment => segment.text).join('')

export const orchestratorContextHint = (newSelected: boolean) => hintText(orchestratorContextHintSegments(newSelected))

export const orchestratorGlobalHotkeyHint = () => hintText(orchestratorGlobalHotkeyHintSegments())

export const orchestratorHintSegmentColor = (t: Theme, role: OrchestratorHintRole) => {
  if (role === 'hotkey') {
    return t.color.accent
  }

  if (role === 'label') {
    return t.color.label
  }

  return t.color.muted
}

// Delegates to the shared list-row primitive so the session switcher and the
// completions popover cannot disagree about what "selected" looks like.
// (`selectionBg` remains the TEXT-selection highlight — a different semantic.)
export const selectedSessionRowStyle = (t: Theme) => ({
  backgroundColor: listRowStyle(t, true).backgroundColor,
  color: listRowStyle(t, true).color
})

export const newSessionMarkerColor = (t: Theme, selected: boolean) =>
  selected ? selectedSessionRowStyle(t).color : t.color.label

export const newSessionRowIndex = (sessionCount: number) => Math.max(0, sessionCount)

export const isNewSessionRow = (index: number, sessionCount: number) => index >= newSessionRowIndex(sessionCount)

export const canTypeOrchestratorPrompt = (index: number, sessionCount: number) => isNewSessionRow(index, sessionCount)

export const clampOrchestratorSelection = (index: number, sessionCount: number) =>
  Math.max(0, Math.min(index, newSessionRowIndex(sessionCount)))

export const currentSessionSelectionIndex = (
  sessions: readonly SessionActiveItem[],
  currentSessionId: null | string
) => {
  const index = sessions.findIndex(s => Boolean(s.current) || (!!currentSessionId && s.id === currentSessionId))

  return index >= 0 ? index : 0
}

export const orchestratorVisibleRowIndexes = (sessionCount: number, selected: number, visible = VISIBLE) => {
  const total = Math.max(0, sessionCount) + 1
  const clamped = clampOrchestratorSelection(selected, sessionCount)
  const offset = windowOffset(total, clamped, visible)
  const count = Math.min(visible, total - offset)

  return Array.from({ length: count }, (_, i) => offset + i)
}

export type CloseFallback = { action: 'activate'; sessionId: string } | { action: 'new' } | { action: 'stay' }

export const closeFallbackAfterClose = (
  closedId: string,
  currentSessionId: null | string,
  remaining: readonly SessionActiveItem[]
): CloseFallback => {
  if (!currentSessionId || closedId !== currentSessionId) {
    return { action: 'stay' }
  }

  const next = remaining.find(s => s.id !== closedId)

  return next ? { action: 'activate', sessionId: next.id } : { action: 'new' }
}

export const draftModelArgFromPickerValue = (value: string) => {
  return sessionScopedModelArg(value)
}

export const draftModelNameFromArg = (value: string) => {
  const parts = draftModelArgFromPickerValue(value).split(/\s+/).filter(Boolean)
  const modelParts: string[] = []

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]!

    if (part === '--provider' || part === '--reasoning') {
      i++

      continue
    }

    if (part.startsWith('--')) {
      continue
    }

    modelParts.push(part)
  }

  return modelParts.join(' ').trim()
}

export const draftModelDisplayLabel = (value: string) => {
  const modelName = draftModelNameFromArg(value)

  return modelName ? shortModel(modelName) : messages().pickers.session.currentOrDefault
}

export type OrchestratorRowClickAction = { action: 'activate'; sessionId: string } | { action: 'select-new' }

export const orchestratorRowClickAction = (
  index: number,
  sessions: readonly SessionActiveItem[]
): OrchestratorRowClickAction => {
  const target = sessions[index]

  return target && !isNewSessionRow(index, sessions.length)
    ? { action: 'activate', sessionId: target.id }
    : { action: 'select-new' }
}

export const draftTitleFromPrompt = (prompt: string, max = TITLE_MAX) => {
  const compact = prompt.replace(/\s+/g, ' ').trim()

  if (compact.length <= max) {
    return compact
  }

  return `${compact.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

function OrchestratorHintSegments({ segments, t }: OrchestratorHintTextProps) {
  return (
    <>
      {segments.map((segment, index) => (
        <Text color={orchestratorHintSegmentColor(t, segment.role)} key={`${segment.role}-${index}`}>
          {segment.text}
        </Text>
      ))}
    </>
  )
}

function OrchestratorHintText({ segments, t }: OrchestratorHintTextProps) {
  return (
    <Text color={orchestratorHintSegmentColor(t, 'text')} wrap="truncate-end">
      <OrchestratorHintSegments segments={segments} t={t} />
    </Text>
  )
}

interface ResumeMetadata {
  cwd?: string | null
  git_repo_root?: string | null
  git_branch?: string | null
  started_at?: number | null
  last_activity_at?: number | null
  message_count?: number | null
}

type ResumeSelector = { workspace: string; workspace_cwd?: never } | { workspace?: never; workspace_cwd: string }

const resolveResumeSelector = async (gw: GatewayClient, sessionId: string | null): Promise<ResumeSelector | null> => {
  if (!sessionId || !gw.sessionListWorkspaceCwd) {
    return null
  }

  const info = await gw.request<Pick<ResumeMetadata, 'cwd' | 'git_repo_root'>>('session.info', { session_id: sessionId })

  if (info?.git_repo_root?.trim()) {
    return { workspace: info.git_repo_root }
  }

  return info?.cwd?.trim() ? { workspace_cwd: info.cwd } : null
}

const resumeColumnsPerLine = (width: number) => (width >= 90 ? 6 : width >= 62 ? 3 : width >= 38 ? 2 : 1)

function ResumeRowMetadata({ row, width, color }: { row: ResumeMetadata; width: number; color: string }) {
  const S = useT().pickers.session
  const labels = S.resumeWorkspace
  const values = [
    [labels.cwd, row.cwd],
    [labels.repo, row.git_repo_root],
    [labels.branch, row.git_branch],
    [labels.started, relativeSessionAge(row.started_at ?? undefined)],
    [labels.activity, relativeSessionAge(row.last_activity_at ?? undefined)],
    row.message_count == null ? [labels.messages, null] : ['', S.row.messageCount(row.message_count)]
  ]
  const columns = resumeColumnsPerLine(width)
  const columnWidth = Math.max(1, Math.floor((width - 4) / columns))

  return Array.from({ length: Math.ceil(values.length / columns) }, (_, line) => (
    <Box key={line} paddingLeft={3}>
      {values.slice(line * columns, (line + 1) * columns).map(([label, value], index) => (
        <Box key={index} paddingRight={1} width={columnWidth}>
          <Text color={color} wrap="truncate-end">
            {label ? `${label}: ` : ''}{value || '—'}
          </Text>
        </Box>
      ))}
    </Box>
  ))
}

export function ActiveSessionSwitcher({
  currentSessionId,
  gw,
  maxWidth,
  onCancel,
  onClose,
  onNew,
  onNewPrompt,
  onResume,
  onSelect,
  resumePicker = false,
  t
}: ActiveSessionSwitcherProps) {
  const [items, setItems] = useState<(SessionActiveItem & ResumeMetadata)[]>([])
  const [history, setHistory] = useState<(SessionListRow & ResumeMetadata)[]>([])
  const [err, setErr] = useState('')
  // A close/delete failure: kept apart from `err`, which every quiet poll rewrites.
  const [actionErr, setActionErr] = useState('')
  const [sel, setSel] = useState(0)
  const [loading, setLoading] = useState(true)
  const [scopeEpoch, setScopeEpoch] = useState(0)
  const scopeEpochRef = useRef(0)
  const filterActive = scopeEpoch % 2 === 1
  const [filterUnavailable, setFilterUnavailable] = useState(false)
  const scopeRef = useRef<ResumeSelector | null>(null)
  const requestRef = useRef({ seq: 0, busy: false })
  const pickerFields = resumePicker && gw.sessionListPicker
  const workspaceSupported = resumePicker && gw.sessionListWorkspaceCwd
  const [draft, setDraft] = useState('')
  const [draftModel, setDraftModel] = useState('')
  const [pickingModel, setPickingModel] = useState(false)
  const [closingId, setClosingId] = useState('')
  // When non-null, the user pressed `d` on this (history) session and we await
  // a second `d` to confirm deletion. Tracked by session id (not row index) so
  // the 1.5s live-status poll re-indexing rows can't redirect the delete to a
  // different session. Any other key cancels the prompt.
  const [confirmDelete, setConfirmDelete] = useState<null | string>(null)
  const T = useT()
  const S = T.pickers.session
  const C = T.pickers.common
  const initialSelectionAppliedRef = useRef(false)
  // Holds the RAW `session.list` results (pre-dedupe). The quiet 1.5s poll
  // re-derives the resumable list from this against the latest live set, so a
  // session that was hidden while live reappears in history once it closes —
  // without re-querying the DB. Only refreshed on a full (includeHistory) load.
  const rawHistoryRef = useRef<(SessionListRow & ResumeMetadata)[]>([])
  // Mirror the displayed lists so the async poll can re-anchor the selection to
  // the *same* row (by session id) after live sessions appear/disappear, rather
  // than keeping a now-stale flat index.
  const itemsRef = useRef<SessionActiveItem[]>([])
  const historyDisplayRef = useRef<SessionListRow[]>([])
  const { stdout } = useStdout()
  // Optional maxWidth lets grid layouts hand the switcher its cell budget.
  const preferredWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6))
  const terminalWidth = (stdout?.columns ?? 80) - 2
  const width = clampOverlayWidth(
    preferredWidth,
    resumePicker ? Math.min(maxWidth ?? terminalWidth, terminalWidth) : maxWidth
  )
  const promptColumns = Math.max(resumePicker ? 1 : 20, width - 11)
  const linesPerRow = 1 + 6 / resumeColumnsPerLine(width)
  const visible = resumePicker
    ? Math.max(1, Math.min(VISIBLE, Math.floor(((stdout?.rows ?? 30) - 11) / linesPerRow)))
    : VISIBLE

  const clearRows = useCallback(() => {
    rawHistoryRef.current = []
    initialSelectionAppliedRef.current = false
    setItems([])
    setHistory([])
    setSel(0)
  }, [])

  // Rows are [new][live…][history…]: the "+ new" row is pinned first (index 0,
  // always rendered) and the live+history list is windowed below it. `total`
  // is the count of selectable rows (incl. the new row).
  const liveCount = items.length
  const histCount = history.length
  const listLen = liveCount + histCount
  const total = listLen + 1
  const rowKind = useCallback((index: number) => sessionRowKindAt(index, liveCount), [liveCount])

  const load = useCallback(
    // `quiet` skips the loading spinner (used by the live-status poll);
    // `includeHistory` re-queries the resumable DB list (skipped on the 1.5s
    // poll, which only needs fresh live-session status).
    async (quiet = false, includeHistory = true) => {
      // A timer from the previous mode can fire before its effect is cleaned up.
      if (scopeEpoch !== scopeEpochRef.current ||
          (resumePicker && !includeHistory && (requestRef.current.busy || (filterActive && !scopeRef.current)))) {
        return []
      }

      const seq = ++requestRef.current.seq
      const stale = () => resumePicker && seq !== requestRef.current.seq
      requestRef.current.busy = true

      if (!quiet) {
        setLoading(true)
      }

      try {
        let selector = filterActive ? scopeRef.current : null

        if (filterActive && includeHistory) {
          try {
            selector = workspaceSupported ? await resolveResumeSelector(gw, currentSessionId) : null
          } catch {
            selector = null
          }

          if (stale()) {
            return []
          }

          if (
            selector?.workspace !== scopeRef.current?.workspace ||
            selector?.workspace_cwd !== scopeRef.current?.workspace_cwd
          ) {
            clearRows()
          }

          scopeRef.current = selector
          setFilterUnavailable(!selector)
        }

        if (filterActive && (!selector || !gw.sessionListWorkspaceCwd)) {
          scopeRef.current = null
          clearRows()
          setFilterUnavailable(true)
          setErr('')
          setLoading(false)

          return []
        }

        const listParams = { limit: 200, ...(pickerFields ? { picker: true } : {}), ...selector }
        // Fetch independently (allSettled) so a failing session.list can't
        // wipe the live-session list: live sessions still render and the
        // resumable history degrades on its own.
        const canonicalList = gw.isCanonical
          ? gw.request<{ scope?: string; sessions?: (SessionListRow & ResumeMetadata & { running?: boolean })[] }>('session.list', listParams)
          : null

        const [liveRes, histRes] = await Promise.allSettled([
          canonicalList
            ? canonicalList.then(value => ({
                sessions: value.sessions?.map(row => ({
                  ...row,
                  current: row.id === currentSessionId,
                  status: row.running ? ('working' as const) : ('idle' as const)
                }))
              }))
            : gw.request<SessionActiveListResponse>('session.active_list', { current_session_id: currentSessionId }),
          includeHistory
            ? gw.request<SessionListResult>('session.list', {
                ...listParams,
                ...(canonicalList ? { scope: 'stored' } : {})
              })
            : Promise.resolve(null)
        ])

        if (stale()) {
          return []
        }

        const r = liveRes.status === 'fulfilled' ? asRpcResult<SessionActiveListResponse>(liveRes.value) : null

        if (!r) {
          setErr(messages().pickers.common.invalidResponse('session.active_list'))
          setLoading(false)

          return []
        }

        const next = r.sessions ?? []

        // Surface a garbled/failed session.list rather than silently blanking
        // the resumable section; keep the last good raw history so a transient
        // failure doesn't wipe it.
        let histError = ''

        if (includeHistory) {
          if (histRes.status === 'fulfilled') {
            const parsedHist = asRpcResult<SessionListResult>(histRes.value)

            if (parsedHist) {
              rawHistoryRef.current = parsedHist.sessions ?? []
            } else {
              histError = messages().pickers.common.invalidResponse('session.list')
            }
          } else {
            histError = messages().pickers.session.errors.couldNotLoadResumable
          }
        }

        const hist = resumableHistory(rawHistoryRef.current, next)
        const initializeSelection = !initialSelectionAppliedRef.current
        initialSelectionAppliedRef.current = true
        const maxSel = next.length + hist.length // == total - 1 (new row is index 0)

        setItems(next)
        setHistory(hist)
        // Re-anchor selection to the same row by identity (the live list can
        // grow/shrink between polls, which would otherwise drift a flat index).
        setSel(s => {
          if (initializeSelection) {
            // Land on the current live session (shifted +1 past the pinned new
            // row); with no live sessions, start on the new row itself.
            return next.length ? Math.min(currentSessionSelectionIndex(next, currentSessionId) + 1, maxSel) : 0
          }

          if (s <= 0) {
            return 0 // "+ new" row
          }

          const prevItems = itemsRef.current
          const prevHist = historyDisplayRef.current
          const clamp = () => Math.max(0, Math.min(s, maxSel))

          if (s - 1 < prevItems.length) {
            const id = prevItems[s - 1]?.id
            const i = id ? next.findIndex(x => x.id === id) : -1

            return i >= 0 ? i + 1 : clamp()
          }

          const id = prevHist[s - 1 - prevItems.length]?.id
          const i = id ? hist.findIndex(x => x.id === id) : -1

          return i >= 0 ? 1 + next.length + i : clamp()
        })
        if (includeHistory || !resumePicker) {
          setErr(histError)
        }
        setLoading(false)

        return next
      } catch (e: unknown) {
        if (!stale()) {
          setErr(rpcErrorMessage(e))
          setLoading(false)
        }

        return []
      } finally {
        if (!stale()) {
          requestRef.current.busy = false
        }
      }
    },
    [clearRows, currentSessionId, filterActive, gw, pickerFields, resumePicker, scopeEpoch, workspaceSupported]
  )

  useEffect(() => {
    itemsRef.current = items
    historyDisplayRef.current = history
  }, [items, history])

  useEffect(() => {
    let toggleTimer: ReturnType<typeof setTimeout> | undefined

    if (scopeEpoch > 0) {
      const seq = requestRef.current.seq
      toggleTimer = setTimeout(() => {
        if (seq === requestRef.current.seq) {
          void load()
        }
      }, RESUME_TOGGLE_DEBOUNCE_MS)
    } else {
      void load()
    }

    const timer = setInterval(() => void load(true, false), 1500)

    return () => {
      clearTimeout(toggleTimer)
      clearInterval(timer)
      requestRef.current.seq++
      requestRef.current.busy = false
    }
  }, [load, scopeEpoch])

  const submitDraft = useCallback(
    (value: string) => {
      const prompt = value.trim()

      if (!prompt) {
        return
      }

      setDraft('')
      onNewPrompt(prompt, draftModel || undefined)
    },
    [draftModel, onNewPrompt]
  )

  const closeSelected = useCallback(async () => {
    const target = items[sel - 1]

    if (!target || rowKind(sel) !== 'live' || closingId) {
      return
    }

    const scopeAtClose = scopeEpochRef.current
    setActionErr('')
    setClosingId(target.id)

    try {
      const result = await onClose(target.id)
      const closed = Boolean(result?.closed ?? result?.ok)

      if (!closed) {
        // A canonical view closes only what it holds: a null answer means no close was attempted, not
        // that main refused one. Only a receipt can say the session was already closed.
        setActionErr(
          gw.isCanonical && result === null
            ? messages().pickers.session.errors.notAttached
            : messages().pickers.session.errors.alreadyClosed
        )

        return
      }

      if (scopeAtClose !== scopeEpochRef.current) {
        return
      }

      const remaining = await load(true)

      if (scopeAtClose !== scopeEpochRef.current) {
        return
      }

      const fallback = closeFallbackAfterClose(target.id, currentSessionId, remaining)

      if (fallback.action === 'activate') {
        onSelect(fallback.sessionId)
      } else if (fallback.action === 'new') {
        onNew()
      } else {
        setSel(s => Math.max(0, Math.min(s, remaining.length + history.length)))
      }
    } catch (e: unknown) {
      setActionErr(rpcErrorMessage(e))
    } finally {
      setClosingId('')
    }
  }, [closingId, currentSessionId, gw.isCanonical, history.length, items, load, onClose, onNew, onSelect, rowKind, sel])

  const removeDeleted = useCallback(
    (id: string) => {
      rawHistoryRef.current = rawHistoryRef.current.filter(h => h.id !== id)
      setHistory(prev => prev.filter(h => h.id !== id))
      setSel(s => Math.max(0, Math.min(s, items.length + history.length - 1)))
    },
    [history.length, items.length]
  )

  const keepView = useCallback((resolved: string) => resolved === currentSessionId, [currentSessionId])
  const { deletePending, deleting, performDelete } = useSessionDelete(gw, removeDeleted, setActionErr, keepView)

  const handleRowClick = useCallback(
    (index: number) => (event: { stopImmediatePropagation?: () => void }) => {
      event.stopImmediatePropagation?.()

      // Keys are held while a delete is pending; a click must not open the row mid-delete either.
      if (deletePending()) {
        return
      }

      const kind = rowKind(index)
      const clamped = Math.max(0, Math.min(index, total - 1))

      if (kind === 'live') {
        setSel(clamped)
        onSelect(items[index - 1]!.id)

        return
      }

      if (kind === 'history') {
        setSel(clamped)
        onResume(history[index - 1 - items.length]!.id)

        return
      }

      setSel(0)
    },
    [deletePending, history, items, onResume, onSelect, rowKind, total]
  )

  const selectedKind = rowKind(sel)
  const newSelected = selectedKind === 'new'
  const draftHasText = Boolean(draft.trim())

  useInput((ch, key) => {
    if (pickingModel || deletePending()) {
      return
    }

    const lower = ch?.toLowerCase() ?? ''
    const isCtrl = (letter: string) => key.ctrl && (lower === letter || ch === ctrlChar(letter))

    if (resumePicker && isCtrl('t')) {
      requestRef.current.seq++
      scopeRef.current = null
      clearRows()
      setConfirmDelete(null)
      setActionErr('')
      setErr('')
      setFilterUnavailable(false)
      setLoading(true)
      setScopeEpoch(++scopeEpochRef.current)

      return
    }

    // Two-press history delete: once armed, only a second `d` deletes; any
    // other key cancels the prompt (mirrors the standalone resume picker).
    if (confirmDelete !== null) {
      if (ch?.toLowerCase() === 'd') {
        setConfirmDelete(null)
        performDelete(history.find(h => h.id === confirmDelete))
      } else {
        setConfirmDelete(null)
      }

      return
    }

    if (key.escape) {
      return onCancel()
    }

    if (isCtrl('n')) {
      return onNew()
    }

    if (isCtrl('r')) {
      void load()

      return
    }

    if (key.tab) {
      if (newSelected) {
        setPickingModel(true)
      }

      return
    }

    if (isCtrl('d')) {
      if (selectedKind === 'live') {
        void closeSelected()
      }

      return
    }

    // `d` arms deletion on a resumable history row. (On the New row `d` is
    // captured by the prompt's TextInput, so it never reaches here.)
    if (lower === 'd' && !key.ctrl && selectedKind === 'history') {
      setConfirmDelete(history[sel - 1 - items.length]?.id ?? null)

      return
    }

    if (newSelected && draftHasText) {
      return
    }

    if (key.upArrow && sel > 0) {
      return setSel(s => Math.max(0, s - 1))
    }

    if (key.downArrow && sel < total - 1) {
      return setSel(s => Math.min(total - 1, s + 1))
    }

    if (key.return) {
      if (newSelected) {
        if (!draftHasText) {
          return onNew()
        }

        return
      }

      if (selectedKind === 'live' && items[sel - 1]) {
        return onSelect(items[sel - 1]!.id)
      }

      if (selectedKind === 'history' && history[sel - 1 - items.length]) {
        return onResume(history[sel - 1 - items.length]!.id)
      }
    }
  })

  if (pickingModel) {
    return (
      <ModelPicker
        allowPersistGlobal={false}
        gw={gw}
        onCancel={() => setPickingModel(false)}
        onSelect={value => {
          setDraftModel(draftModelArgFromPickerValue(value))
          setPickingModel(false)
        }}
        sessionId={currentSessionId}
        t={t}
      />
    )
  }

  if (loading && !resumePicker) {
    return <Text color={t.color.muted}>{S.loading}</Text>
  }

  // The "+ new" row (sel 0) is pinned at the top so it's always visible; the
  // live + history list is windowed beneath it.
  const listSel = sel > 0 ? sel - 1 : 0
  const offset = windowOffset(listLen, listSel, visible)
  const visibleCount = Math.max(0, Math.min(visible, listLen - offset))
  const visibleRows = Array.from({ length: visibleCount }, (_, k) => offset + k + 1)

  const newSelectedRow = sel === 0
  const newRowStyle = newSelectedRow ? selectedSessionRowStyle(t) : null
  const newRowTextColor = newRowStyle?.color
  const newRowMarkerColor = newSessionMarkerColor(t, newSelectedRow)
  const promptTitle = draftTitleFromPrompt(draft) || S.row.startNew

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={t.color.accent}>
        {S.title}
      </Text>
      <Text color={t.color.muted}>{sessionsCountLabel(items.length, history.length)}</Text>

      {resumePicker && (
        <Text color={t.color.muted}>
          <Text color={t.color.accent}>Ctrl+T</Text>{width < 42 ? '\n' : ' · '}
          {filterActive ? (filterUnavailable ? S.resumeWorkspace.unavailable : S.resumeWorkspace.on) : S.resumeWorkspace.off}
        </Text>
      )}
      {resumePicker && loading && <Text color={t.color.muted}>{S.loading}</Text>}
      {(actionErr || err) && <Text color={t.color.label}>{C.error(actionErr || err)}</Text>}

      {resumePicker ? (
        <Box backgroundColor={newRowStyle?.backgroundColor} onClick={handleRowClick(0)} width="100%">
          <Text color={newRowTextColor ?? t.color.label} wrap="truncate-end">
            {newSelectedRow ? '▸ ' : '  '}{S.newRowMarker} {promptTitle}
          </Text>
        </Box>
      ) : (
        <Box backgroundColor={newRowStyle?.backgroundColor} flexDirection="row" onClick={handleRowClick(0)} width="100%">
          <Text bold={newSelectedRow} color={newRowTextColor ?? t.color.muted}>
            {newSelectedRow ? '▸ ' : '  '}
          </Text>

          <Box {...fixedSessionColumnStyle()} width={5}>
            <Text bold={newSelectedRow} color={newRowMarkerColor}>
              {'+'.padStart(2)}
            </Text>
          </Box>

          <Box {...fixedSessionColumnStyle()} width={11}>
            <Text bold={newSelectedRow} color={newRowMarkerColor} wrap="truncate-end">
              {S.row.new}
            </Text>
          </Box>

          <Box {...fixedSessionColumnStyle()} width={11}>
            <Text color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
              {S.row.draft}
            </Text>
          </Box>

          <Box {...fixedSessionColumnStyle()} width={18}>
            <Text color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
              {draftModelDisplayLabel(draftModel)}
            </Text>
          </Box>

          <Box flexGrow={1} flexShrink={1} minWidth={0}>
            <Text bold={newSelectedRow} color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
              {promptTitle}
            </Text>
          </Box>
        </Box>
      )}

      {offset > 0 && <Text color={t.color.muted}>{C.moreAbove(offset)}</Text>}
      {!listLen && !loading && !filterUnavailable && (
        <Text color={t.color.muted}>{filterActive ? S.resumeWorkspace.noMatches : S.noOtherSessions}</Text>
      )}

      {visibleRows.map(i => {
        const selected = sel === i
        const selectedStyle = selected ? selectedSessionRowStyle(t) : null
        const rowTextColor = selectedStyle?.color
        const kind = rowKind(i)

        if (resumePicker) {
          const row = kind === 'history' ? history[i - 1 - items.length]! : items[i - 1]!
          const pendingDelete = confirmDelete === row.id
          const title = pendingDelete ? S.row.pressDAgain
            : deleting && selected ? S.row.deleting
            : closingId === row.id ? S.row.closing
            : row.title || row.preview || S.row.untitled
          const current = row.id === currentSessionId
          const color = rowTextColor ?? t.color.muted
          const status = kind === 'live' ? (items[i - 1]?.status ?? 'idle') : null

          return (
            <Box backgroundColor={selectedStyle?.backgroundColor} flexDirection="column" key={row.id}
              onClick={handleRowClick(i)} width="100%">
              <Text bold={selected} color={pendingDelete ? t.color.label : color} wrap="truncate-end">
                {selected ? '▸ ' : '  '}{i}. {status && `${STATUS_GLYPH[status] ?? '·'} `}{title}
                {current ? ` (${S.row.current})` : ''}
              </Text>
              <ResumeRowMetadata color={color} row={row} width={width} />
            </Box>
          )
        }

        if (kind === 'history') {
          const h = history[i - 1 - items.length]!
          const pendingDelete = confirmDelete === h.id

          const title = pendingDelete
            ? S.row.pressDAgain
            : deleting && selected
              ? S.row.deleting
              : h.title || h.preview || S.row.untitled

          return (
            <Box
              backgroundColor={selectedStyle?.backgroundColor}
              flexDirection="row"
              key={h.id}
              onClick={handleRowClick(i)}
              width="100%"
            >
              <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                {selected ? '▸ ' : '  '}
              </Text>

              <Box {...fixedSessionColumnStyle()} width={5}>
                <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                  {String(i).padStart(2)}.
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={11}>
                <Text bold={selected} color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {h.id}
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={11}>
                <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {relativeSessionAge(h.started_at)}
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={18}>
                <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {S.row.messageCount(h.message_count ?? 0)}
                </Text>
              </Box>

              <Box flexGrow={1} flexShrink={1} minWidth={0}>
                <Text
                  bold={selected}
                  color={pendingDelete ? t.color.label : (rowTextColor ?? t.color.muted)}
                  wrap="truncate-end"
                >
                  {title}
                </Text>
              </Box>
            </Box>
          )
        }

        const s = items[i - 1]!
        const status = s.status ?? 'idle'
        const current = s.current || s.id === currentSessionId
        const title = closingId === s.id ? S.row.closing : s.title || s.preview || S.row.untitled

        return (
          <Box
            backgroundColor={selectedStyle?.backgroundColor}
            flexDirection="row"
            key={s.id}
            onClick={handleRowClick(i)}
            width="100%"
          >
            <Text bold={selected} color={rowTextColor ?? t.color.muted}>
              {selected ? '▸ ' : '  '}
            </Text>

            <Box {...fixedSessionColumnStyle()} width={5}>
              <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                {String(i).padStart(2)}.
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={11}>
              <Text
                bold={selected}
                color={rowTextColor ?? (current ? t.color.label : t.color.muted)}
                wrap="truncate-end"
              >
                {current ? S.row.current : s.id}
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={11}>
              <Text
                color={
                  rowTextColor ??
                  (status === 'working' ? t.color.ok : status === 'waiting' ? t.color.label : t.color.muted)
                }
                wrap="truncate-end"
              >
                {STATUS_GLYPH[status] ?? '·'} {sessionStatusLabel(status)}
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={18}>
              <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                {shortModel(s.model)}
              </Text>
            </Box>

            <Box flexGrow={1} flexShrink={1} minWidth={0}>
              <Text bold={selected} color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                {title}
              </Text>
            </Box>
          </Box>
        )
      })}

      {offset + visible < listLen && <Text color={t.color.muted}>{C.moreBelow(listLen - offset - visible)}</Text>}

      {newSelected ? (
        <>
          <Box marginTop={1}>
            <Text color={t.color.label}>{S.promptLabel}</Text>
            <TextInput
              color={t.color.text}
              columns={promptColumns}
              onChange={setDraft}
              onSubmit={submitDraft}
              value={draft}
            />
          </Box>
          <OrchestratorHintText segments={orchestratorContextHintSegments(true)} t={t} />
          <Text color={t.color.muted} wrap="truncate-end">
            {S.draftModel(draftModelDisplayLabel(draftModel))}
          </Text>
        </>
      ) : (
        <Box flexDirection="column" marginTop={1}>
          <OrchestratorHintText
            segments={
              selectedKind === 'history' ? resumeRowContextHintSegments() : orchestratorContextHintSegments(false)
            }
            t={t}
          />
          <Text color={t.color.muted} wrap="truncate-end">
            {S.selectNewPrefix}
            <Text color={newSessionMarkerColor(t, false)}>{S.newRowMarker}</Text>
            {S.selectNewSuffix}
          </Text>
        </Box>
      )}

      <OrchestratorHintText segments={orchestratorGlobalHotkeyHintSegments()} t={t} />
    </Box>
  )
}

interface OrchestratorHintTextProps {
  segments: readonly OrchestratorHintSegment[]
  t: Theme
}

interface ActiveSessionSwitcherProps {
  currentSessionId: null | string
  gw: GatewayClient
  maxWidth?: number
  onCancel: () => void
  onClose: (id: string) => Promise<null | SessionCloseResponse>
  onNew: () => void
  onNewPrompt: (prompt: string, modelArg?: string) => void
  onResume: (id: string) => void
  onSelect: (id: string) => void
  resumePicker?: boolean
  t: Theme
}
