import { getOverlayState, patchOverlayState } from './overlayStore.js'
import { getPendingApprovalRequest, respondToServerRequest } from './serverRequestStore.js'
import { patchTurnState } from './turnStore.js'
import { patchUiState } from './uiStore.js'

/** Project the FIFO head; request identity and lifetime stay in the native request store. */
export function refreshApprovalPrompt(): void {
  const request = getPendingApprovalRequest()

  if (!request) {
    patchOverlayState({ approval: null })

    return
  }

  const p = request.params

  const choices = Array.isArray(p.choices) && p.choices.length > 0
    ? p.choices.filter((choice): choice is string => typeof choice === 'string')
    : undefined

  patchOverlayState({
    approval: {
      allowPermanent: p.allow_permanent !== false,
      choices,
      command: typeof p.command === 'string' ? p.command : '',
      description: typeof p.description === 'string' && p.description ? p.description : 'dangerous command',
      requestId: request.id,
      smartDenied: p.smart_denied === true
    }
  })
}

/** One card answers only its own request; advancing the queue never grants consent. */
export function answerApprovalRequest(requestId: string, choice: string): void {
  if (getOverlayState().approval?.requestId !== requestId) {
    return
  }

  const answered = respondToServerRequest(requestId, { choice })
  refreshApprovalPrompt()

  if (answered) {
    patchTurnState({ outcome: choice === 'deny' ? 'denied' : `approved (${choice})` })
    patchUiState({ status: getOverlayState().approval ? 'approval needed' : 'running…' })
  }
}
